import csv
import io
import unittest
from datetime import date
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from tennisd import db
from tennisd.models import AuthToken, Comment, FollowedPlayer, Match, Player, ProfileImage, Report, Review, User
from tennisd.security import issue_token
import test_app as fixture


class AccountLifecycleTests(unittest.TestCase):
    tearDown=fixture.TennisdFlows.tearDown
    register=fixture.TennisdFlows.register
    token=fixture.TennisdFlows.token

    def setUp(self):
        fixture.TennisdFlows.setUp(self);self.register('alice')
        with self.app.app_context():self.uid=db.session.scalar(select(User.id).where(User.username=='alice'))

    def test_deletion_handles_other_users_reports_without_deleting_their_content(self):
        with self.app.app_context():
            bob=User(username='bob',email='bob@example.test',display_name='Bob',password_hash='unused')
            db.session.add(bob);db.session.flush();bid=bob.id
            review=Review(user_id=bid,match_id=self.match_id,watched_on=date(2026,1,1),body='Bob retains this review',is_public=True)
            db.session.add(review);db.session.flush();rid=review.id
            mine=Comment(user_id=self.uid,review_id=rid,body='Alice comment removed')
            theirs=Comment(user_id=bid,review_id=rid,body='Bob comment preserved')
            db.session.add_all((mine,theirs));db.session.flush();cid=mine.id;keep=theirs.id
            db.session.add(Report(reporter_id=bid,comment_id=cid,reason='spam'))
            kept_report=Report(reporter_id=bid,comment_id=keep,reason='other')
            db.session.add(kept_report);db.session.commit();kept_report_id=kept_report.id
        self.assertEqual(self.client.post('/settings/delete-account',data={'csrf_token':self.token(),'username':'alice','password':'wrong'}).status_code,302)
        with self.app.app_context():self.assertIsNotNone(db.session.get(Comment,cid))
        self.assertEqual(self.client.post('/settings/delete-account',data={'csrf_token':self.token(),'username':'alice','password':'long-test-password'}).status_code,302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(User,self.uid));self.assertIsNone(db.session.get(Comment,cid))
            self.assertIsNotNone(db.session.get(User,bid));self.assertIsNotNone(db.session.get(Review,rid))
            self.assertIsNotNone(db.session.get(Comment,keep));self.assertIsNotNone(db.session.get(Report,kept_report_id))

    def test_settings_password_change_revokes_old_reset_links_and_sessions(self):
        with self.app.app_context():
            raw=issue_token(db.session.get(User,self.uid),'reset',1800)
        other=self.app.test_client()
        with other.session_transaction() as session:session['_user_id']=f'{self.uid}:0';session['_fresh']=True
        self.client.get('/settings')
        bad=self.client.post('/settings',data={'csrf_token':self.token(),'display_name':'Alice','current_password':'bad','new_password':'new-long-password'})
        self.assertEqual(bad.status_code,200)
        self.assertEqual(self.client.get('/reset-password/'+raw).status_code,200)
        r=self.client.post('/settings',data={'csrf_token':self.token(),'display_name':'Alice','current_password':'long-test-password','new_password':'new-long-password'})
        self.assertEqual(r.status_code,302)
        self.assertEqual(self.client.get('/reset-password/'+raw).location,'/forgot-password')
        self.assertEqual(other.get('/settings').status_code,302)
        self.assertEqual(self.client.get('/settings').status_code,200)
        with self.app.app_context():
            self.assertEqual(db.session.scalars(select(AuthToken).where(AuthToken.user_id==self.uid)).all(),[])
            self.assertTrue(db.session.get(User,self.uid).check_password('new-long-password'))

    def test_export_namesakes_ids_csv_controls_and_photo_metadata(self):
        text='\t=1+1'
        with self.app.app_context():
            user=db.session.get(User,self.uid);user.bio='  @SUM(1)'
            user.profile_image=ProfileImage(mime_type='image/webp',image_data=b'fixture binary not exported')
            players=[Player(id=f'export-person-{i}',name='Same Name',tour='ATP') for i in range(2)]
            db.session.add_all(players);db.session.flush()
            db.session.add_all(FollowedPlayer(user_id=self.uid,player_id=p.id) for p in players)
            db.session.add(Review(user_id=self.uid,match_id=self.match_id,watched_on=date(2026,1,1),body=text,is_public=False))
            db.session.commit()
        loaded=[]
        def load(session,obj):loaded.append(type(obj))
        event.listen(Session,'loaded_as_persistent',load)
        try:response=self.client.get('/settings/export')
        finally:event.remove(Session,'loaded_as_persistent',load)
        payload=response.get_json()
        self.assertEqual(payload['schema_version'],1)
        self.assertEqual(payload['followed_players'],['Same Name','Same Name'])
        self.assertEqual([p['id'] for p in payload['followed_player_profiles']],['export-person-0','export-person-1'])
        self.assertTrue(payload['has_profile_photo']);self.assertNotIn(ProfileImage,loaded)
        self.assertEqual(payload['diary'][0]['review'],text)
        rows=list(csv.DictReader(io.StringIO(self.client.get('/settings/export.csv').get_data(as_text=True))))
        self.assertEqual(next(r for r in rows if r['record_type']=='account')['bio'],"'  @SUM(1)")
        self.assertEqual(next(r for r in rows if r['record_type']=='diary')['review'],"'"+text)
        self.assertEqual([r['player_id'] for r in rows if r['record_type']=='followed_player'],['export-person-0','export-person-1'])
        self.assertIn('no-store',response.headers['Cache-Control'])

    def test_export_has_fixed_query_budget_for_many_participants(self):
        def export_count():
            queries=[]
            def before(conn,cursor,statement,params,context,many):
                if statement.lstrip().upper().startswith('SELECT'):queries.append(statement)
            with self.app.app_context():engine=db.engine
            event.listen(engine,'before_cursor_execute',before)
            try:r=self.client.get('/settings/export')
            finally:event.remove(engine,'before_cursor_execute',before)
            self.assertEqual(r.status_code,200)
            return len(queries)
        with self.app.app_context():
            for i in range(20):
                a=Player(id=f'export-w-{i}',name=f'Winner {i}',tour='ATP')
                b=Player(id=f'export-l-{i}',name=f'Loser {i}',tour='ATP')
                match=Match(id=f'export-match-{i}',winner=a,loser=b,tour='ATP',tournament='Export fixture',surface='Hard',level='A',round='R32',week_start=date(2025,1,1),score='6-4 6-4')
                db.session.add(Review(user_id=self.uid,match=match,watched_on=date(2026,1,1),body='Fixture'))
            db.session.commit()
        populated=export_count()
        # Participants are distinct, so lazy participant loads would add forty
        # queries. The complete export stays within a fixed small query budget.
        self.assertLessEqual(populated,10)
