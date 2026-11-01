"""Bound public discussions, preserve full summaries, and enforce private access."""
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from flask import template_rendered
from sqlalchemy import event, select
from tennisd import create_app, db
from tennisd.models import Comment, Match, Notification, Report, Review, User


class DiscussionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING':True,'SECRET_KEY':'discussion-tests-only',
            'SQLALCHEMY_DATABASE_URI':f"sqlite:///{Path(self.temp.name)/'db'}"})
        self.client = self.app.test_client()
        with self.app.app_context():
            match=db.session.scalar(select(Match).limit(1));self.match_id=match.id
            self.ids=[]
            for i in range(42):
                user=User(username=f'discuss{i}',email=f'discuss{i}@example.test',display_name=f'Member {i}',password_hash='unused')
                review=Review(user=user,match=match,watched_on=date.today(),body=f'NOTE_{i:03}',
                    is_public=i<40,rating_half=10 if i%2 else None,created_at=datetime(2025,1,1,tzinfo=timezone.utc))
                db.session.add(review);db.session.flush();self.ids.append(review.id)
            self.owner_id=db.session.get(Review,self.ids[39]).user_id
            self.other_id=db.session.get(Review,self.ids[0]).user_id
            self.private_owner=db.session.get(Review,self.ids[41]).user_id
            self.reply_ids=[]
            for i in range(47):
                comment=Comment(review_id=self.ids[39],user_id=self.owner_id,body=f'REPLY_{i:03}',created_at=datetime(2025,1,1,tzinfo=timezone.utc))
                db.session.add(comment);db.session.flush();self.reply_ids.append(comment.id)
            db.session.add(Comment(review_id=self.ids[41],user_id=self.private_owner,body='PRIVATE_REPLY'))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():db.session.remove();db.engine.dispose()
        self.temp.cleanup()

    def sign_in(self, user):
        with self.client.session_transaction() as session:
            session['_user_id']=f'{user}:0';session['_fresh']=True;session['csrf_token']='test-csrf'

    def render(self,url):
        captured={};loaded=[]
        def context(sender,template,context,**kw):captured.update(context)
        def load(session,obj):
            if isinstance(obj,(Review,Comment)):loaded.append(type(obj))
        template_rendered.connect(context,self.app)
        from sqlalchemy.orm import Session
        event.listen(Session,'loaded_as_persistent',load)
        try:response=self.client.get(url)
        finally:template_rendered.disconnect(context,self.app);event.remove(Session,'loaded_as_persistent',load)
        self.assertEqual(response.status_code,200)
        return response,captured,loaded

    def test_review_pages_stable_complete_and_bounded_with_full_summary(self):
        seen=[]
        for page,size in ((1,18),(2,18),(3,4)):
            response,c,loaded=self.render(f'/matches/{self.match_id}?page={page}')
            ids=[r.id for r in c['reviews']];seen+=ids
            self.assertEqual(len(ids),size);self.assertLessEqual(loaded.count(Review),18)
            self.assertLessEqual(loaded.count(Comment),3)
            self.assertEqual(c['community']['logs'],40)
            self.assertEqual(c['community']['count'],20)
            self.assertEqual(c['community']['average'],10)
            self.assertEqual(c['community']['distribution'][10],20)
            self.assertNotIn(b'NOTE_040',response.data);self.assertNotIn(b'PRIVATE_REPLY',response.data)
        self.assertEqual(seen,list(reversed(self.ids[:40])))
        response,c,_=self.render(f'/matches/{self.match_id}?page=1')
        self.assertEqual([x.id for x in c['comment_previews'][self.ids[39]]],list(reversed(self.reply_ids[-3:])))
        self.assertEqual(c['comment_totals'][self.ids[39]],47)
        self.assertIn(f'/reviews/{self.ids[39]}#comments'.encode(),response.data)

    def test_reply_pages_have_no_gaps_or_duplicates_and_escape_content(self):
        with self.app.app_context():
            db.session.get(Comment,self.reply_ids[-1]).body='<script>unsafe()</script>';db.session.commit()
        seen=[]
        for page,size in ((1,20),(2,20),(3,7)):
            response,c,loaded=self.render(f'/reviews/{self.ids[39]}?page={page}')
            seen += [x.id for x in c['pagination'].items]
            self.assertEqual(len(c['pagination'].items),size)
            self.assertLessEqual(loaded.count(Comment),20)
            self.assertIn(b'noindex, follow',response.data)
            if page==1:self.assertIn(b'&lt;script&gt;unsafe()',response.data)
        self.assertEqual(seen,list(reversed(self.reply_ids)))

    def test_invalid_pages_clamp_and_empty_threads_render(self):
        for raw,expected in [('nonsense',1),('-4',1),(str(10**100),3)]:
            _,c,_=self.render(f'/reviews/{self.ids[39]}?page={raw}');self.assertEqual(c['pagination'].page,expected)
            _,c,_=self.render(f'/matches/{self.match_id}?page={raw}');self.assertEqual(c['review_page'].page,expected)
        _,c,_=self.render(f'/reviews/{self.ids[0]}');self.assertEqual(c['pagination'].total,0)

    def test_private_thread_is_owner_only_and_cannot_accept_comments(self):
        path=f'/reviews/{self.ids[41]}'
        self.assertEqual(self.client.get(path).status_code,404)
        self.sign_in(self.other_id);self.assertEqual(self.client.get(path).status_code,404)
        self.sign_in(self.private_owner)
        response,c,_=self.render(path);self.assertIn(b'PRIVATE_REPLY',response.data)
        self.assertNotIn(b'class="comment-form"',response.data)
        self.assertEqual(self.client.post(path+'/comments',data={'csrf_token':'test-csrf','body':'hidden'}).status_code,404)

    def test_reply_actions_and_notification_target_the_thread(self):
        self.sign_in(self.other_id)
        path=f'/reviews/{self.ids[39]}'
        response=self.client.post(path+'/comments',data={'csrf_token':'test-csrf','body':'NEW_REPLY'})
        self.assertEqual(response.status_code,302);self.assertTrue(response.location.endswith(path+'#comments'))
        with self.app.app_context():
            comment=db.session.scalar(select(Comment).where(Comment.body=='NEW_REPLY'));cid=comment.id
            note=db.session.scalar(select(Notification).where(Notification.user_id==self.owner_id))
            self.assertEqual(note.url,path+'#comments')
        self.sign_in(self.private_owner)
        self.assertEqual(self.client.post(f'/comments/{cid}/delete',data={'csrf_token':'test-csrf'}).status_code,403)
        self.sign_in(self.owner_id)
        response=self.client.post(f'/comments/{cid}/delete',data={'csrf_token':'test-csrf'})
        self.assertTrue(response.location.endswith(path+'#comments'))

    def test_reports_return_to_accessible_thread_and_remain_deduplicated(self):
        self.sign_in(self.other_id)
        for target, key in (("review",self.ids[39]),("comment",self.reply_ids[-1])):
            for _ in range(2):
                response=self.client.post('/reports',data={'csrf_token':'test-csrf','target':target,'target_id':key,'reason':'spam'})
                self.assertEqual(response.status_code,302)
                self.assertTrue(response.location.endswith(f'/reviews/{self.ids[39]}'))
        with self.app.app_context():self.assertEqual(len(db.session.scalars(select(Report)).all()),2)
        for target,key in (("review",self.ids[41]),):
            self.assertEqual(self.client.post('/reports',data={'csrf_token':'test-csrf','target':target,'target_id':key,'reason':'spam'}).status_code,404)
