"""Private notification history is complete and actions stay owner-scoped."""
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from flask import template_rendered
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from tennisd import create_app, db
from tennisd.models import Comment, Friendship, Match, Notification, Review, User
from tennisd.notifications import notification_targets, safe_notification_url


class NotificationHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app({'TESTING':True,'SECRET_KEY':'history-tests-only',
            'SQLALCHEMY_DATABASE_URI':f"sqlite:///{Path(self.temp.name)/'db'}"})
        self.client=self.app.test_client()
        with self.app.app_context():
            owner=User(username='historyowner',email='owner@example.test',display_name='Owner',password_hash='unused')
            other=User(username='historyother',email='other@example.test',display_name='Other',password_hash='unused')
            db.session.add_all((owner,other));db.session.flush();self.owner,self.other=owner.id,other.id
            match=db.session.scalar(select(Match).limit(1))
            review=Review(user=owner,match=match,watched_on=date.today(),is_public=True,body='Owner review')
            private=Review(user=other,match=match,watched_on=date.today(),is_public=False,body='PRIVATE_OTHER_REVIEW')
            db.session.add_all((review,private));db.session.flush();self.review,self.private=review.id,private.id
            self.notes=[];self.replies=[];self.friends=[]
            for i in range(67):
                note=Notification(user_id=owner.id,kind='test',title=f'UPDATE_{i:03}',body='Owner update',url='/notifications',
                    read_at=datetime(2024,1,1,tzinfo=timezone.utc) if i%3==0 else None,
                    created_at=datetime(2024,1,1,tzinfo=timezone.utc))
                comment=Comment(review_id=review.id,user_id=other.id,body=f'REPLY_{i:03}',created_at=datetime(2024,1,1,tzinfo=timezone.utc))
                db.session.add_all((note,comment));db.session.flush();self.notes.append(note.id);self.replies.append(comment.id)
                friend=User(username=f'historyfriend{i:03}',email=f'friend{i}@example.test',display_name=f'Friend {i}',password_hash='unused')
                request=Friendship(requester=friend,addressee=owner,status='pending',created_at=datetime(2024,1,1,tzinfo=timezone.utc))
                db.session.add(request);db.session.flush();self.friends.append(request.id)
            hidden=Notification(user_id=other.id,kind='test',title='PRIVATE_OTHER_UPDATE',body='private',url='/notifications')
            db.session.add(hidden);db.session.flush();self.hidden=hidden.id
            db.session.add_all((Comment(review_id=review.id,user_id=owner.id,body='SELF_REPLY'),
                Comment(review_id=private.id,user_id=owner.id,body='PRIVATE_OTHER_REPLY')))
            db.session.commit()
        self.sign_in(self.owner)

    def tearDown(self):
        with self.app.app_context():db.session.remove();db.engine.dispose()
        self.temp.cleanup()

    def sign_in(self,user):
        with self.client.session_transaction() as s:
            s['_user_id']=f'{user}:0';s['_fresh']=True;s['csrf_token']='test-csrf'

    def render(self,path):
        captured={};loaded=[]
        def context(sender,template,context,**kw):captured.update(context)
        def load(session,obj):
            if isinstance(obj,(Notification,Comment,Friendship)):loaded.append(type(obj))
        template_rendered.connect(context,self.app);event.listen(Session,'loaded_as_persistent',load)
        try:response=self.client.get(path)
        finally:template_rendered.disconnect(context,self.app);event.remove(Session,'loaded_as_persistent',load)
        self.assertEqual(response.status_code,200)
        return response,captured,loaded

    def test_all_three_histories_are_complete_bounded_and_private(self):
        for tab,expected,model in (('updates',self.notes,Notification),('requests',self.friends,Friendship),('replies',self.replies,Comment)):
            seen=[]
            for page,size in ((1,30),(2,30),(3,7)):
                response,c,loaded=self.render(f'/notifications?tab={tab}&page={page}')
                self.assertEqual(c['totals'],dict(updates=67,requests=67,replies=67))
                self.assertEqual(c['unread'],44)
                self.assertEqual(len(c['pagination'].items),size)
                self.assertLessEqual(loaded.count(model),30)
                seen += [r.id for r in c['pagination'].items]
                self.assertNotIn(b'PRIVATE_OTHER',response.data);self.assertNotIn(b'SELF_REPLY',response.data)
                self.assertIn('no-store',response.headers['Cache-Control'])
                self.assertIn(b'noindex, nofollow',response.data)
            self.assertEqual(seen,list(reversed(expected)))

    def test_unread_filter_invalid_pages_and_navigation_preserve_scope(self):
        for value,expected in ((str(10**100),2),('-1',1),('bad',1)):
            response,c,_=self.render('/notifications?tab=updates&state=unread&page='+value)
            self.assertEqual((c['pagination'].total,c['pagination'].page),(44,expected))
            self.assertTrue(all(r.read_at is None for r in c['in_app']))
            self.assertIn(b'state=unread',response.data)
        _,c,_=self.render('/notifications?tab=invalid&state=invalid');self.assertEqual((c['tab'],c['state']),('updates','all'))

    def test_read_actions_are_csrf_protected_idempotent_and_owner_scoped(self):
        target=self.notes[-2]
        self.assertEqual(self.client.post(f'/notifications/{target}/read').status_code,400)
        self.assertEqual(self.client.post(f'/notifications/{self.hidden}/read',data={'csrf_token':'test-csrf'}).status_code,403)
        self.assertEqual(self.client.post('/notifications/'+str(10**100)+'/read',data={'csrf_token':'test-csrf'}).status_code,404)
        for _ in range(2):
            response=self.client.post(f'/notifications/{target}/read',data={'csrf_token':'test-csrf'})
            self.assertEqual(response.status_code,302)
            with self.app.app_context():
                value=db.session.get(Notification,target).read_at
                if _==0:first=value
                else:self.assertEqual(value,first)
        self.assertEqual(self.client.post('/notifications/read-all').status_code,400)
        self.client.post('/notifications/read-all',data={'csrf_token':'test-csrf'})
        with self.app.app_context():
            self.assertIsNone(db.session.get(Notification,self.hidden).read_at)
            self.assertTrue(all(n.read_at is not None for n in db.session.scalars(select(Notification).where(Notification.user_id==self.owner))))
        _,c,_=self.render('/notifications?state=unread&page='+str(10**100))
        self.assertEqual((c['pagination'].total,c['pagination'].page),(0,1))

    def test_old_review_links_are_repaired_and_private_or_deleted_links_disabled(self):
        with self.app.app_context():
            note=db.session.get(Notification,self.notes[-1]);note.url=f'/matches/old-id#review-{self.review}'
            missing=db.session.get(Notification,self.notes[-2]);missing.url='/reviews/2147483647#comments'
            private=db.session.get(Notification,self.notes[-3]);private.url=f'/reviews/{self.private}#comments'
            unsafe=db.session.get(Notification,self.notes[-4]);unsafe.url='//example.test'
            friend=db.session.get(Notification,self.notes[-5]);friend.kind='friend_request';friend.url='/notifications'
            db.session.commit()
        response,c,_=self.render('/notifications')
        self.assertEqual(c['targets'][self.notes[-1]],f'/reviews/{self.review}#comments')
        self.assertEqual(c['targets'][self.notes[-5]],'/notifications?tab=requests#results')
        for key in self.notes[-4:-1]:self.assertIsNone(c['targets'][key])
        self.assertNotIn(b'href="//example.test',response.data)
        self.assertIn(b'Link unavailable',response.data)
        result=self.client.post(f'/notifications/{self.notes[-1]}/read',data={'csrf_token':'test-csrf'})
        self.assertTrue(result.location.endswith(f'/reviews/{self.review}#comments'))
        result=self.client.post(f'/notifications/{self.notes[-4]}/read',data={'csrf_token':'test-csrf'})
        self.assertTrue(result.location.endswith('/notifications'))

    def test_stored_destinations_cannot_escape_the_site(self):
        for value in ('https://example.test','//example.test','/\\example.test','/%2fexample.test','/%252fexample.test',
                      '/%0d%0aLocation:evil','/x\x00','javascript:alert(1)','/\x7f','/%5cexample.test'):
            self.assertIsNone(safe_notification_url(value),value)
        for value in ('/notifications','/reviews/1?page=2#comments','/matches/abc?q=a%20b'):
            self.assertEqual(safe_notification_url(value),value)

    def test_reply_links_jump_to_exact_stable_page_and_private_comments_are_hidden(self):
        for index,page in ((0,4),(20,3),(66,1)):
            response=self.client.get(f'/comments/{self.replies[index]}')
            self.assertEqual(response.status_code,302)
            self.assertTrue(response.location.endswith(f'/reviews/{self.review}?page={page}#comment-{self.replies[index]}'))
        _,c,_=self.render('/notifications?tab=replies&page=2')
        self.assertEqual(len(c['comments']),30)
        self.assertEqual(self.client.get('/comments/'+str(10**100)).status_code,404)
        with self.app.app_context():hidden=db.session.scalar(select(Comment.id).where(Comment.body=='PRIVATE_OTHER_REPLY'))
        self.assertEqual(self.client.get(f'/comments/{hidden}').status_code,404)
        self.sign_in(self.other)
        self.assertEqual(self.client.get(f'/comments/{hidden}').status_code,302)

    def test_friend_actions_are_recipient_only_and_return_to_requests(self):
        target=self.friends[0]
        self.sign_in(self.other)
        self.assertEqual(self.client.post(f'/friend-requests/{target}/accept',data={'csrf_token':'test-csrf'}).status_code,403)
        self.sign_in(self.owner)
        self.assertEqual(self.client.post(f'/friend-requests/{target}/accept').status_code,400)
        response=self.client.post(f'/friend-requests/{target}/accept',data={'csrf_token':'test-csrf'})
        self.assertTrue(response.location.endswith('/notifications?tab=requests#results'))
        with self.app.app_context():self.assertEqual(db.session.get(Friendship,target).status,'accepted')
        target=self.friends[1]
        response=self.client.post(f'/friend-requests/{target}/decline',data={'csrf_token':'test-csrf'})
        self.assertEqual(response.status_code,302)
        with self.app.app_context():self.assertIsNone(db.session.get(Friendship,target))
        self.assertEqual(self.client.post('/friend-requests/'+str(10**100)+'/accept',data={'csrf_token':'test-csrf'}).status_code,404)
