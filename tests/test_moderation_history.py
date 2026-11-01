import unittest
from datetime import date
from flask import template_rendered
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from tennisd import db
from tennisd.models import FeedbackSubmission, ModerationAction, Poll, PollOption, PollVote, Report, Review, User
import test_app as fixture


class ModerationHistoryTests(unittest.TestCase):
    tearDown=fixture.TennisdFlows.tearDown
    register=fixture.TennisdFlows.register
    token=fixture.TennisdFlows.token

    def setUp(self):
        fixture.TennisdFlows.setUp(self)
        self.app.config['ADMIN_EMAIL']='alice@example.com'
        self.register('alice')
        with self.app.app_context():
            user=db.session.scalar(select(User).where(User.username=='alice'))
            self.uid=user.id
            review=Review(user_id=user.id,match_id=self.match_id,watched_on=date(2026,1,1),is_public=True,body='Reported fixture body')
            db.session.add(review);db.session.flush();self.rid=review.id
            for i in range(67):
                db.session.add(Report(reporter_id=user.id,review_id=review.id,reason='spam',status='open' if i<66 else 'dismissed'))
                db.session.add(FeedbackSubmission(user_id=user.id,category='bug' if i%2 else 'idea',
                    status='new' if i<66 else 'closed',message=f'Fixture feedback {i}',rating=3,
                    page_path='/%2fexample.com' if i==66 else '/matches'))
                db.session.add(ModerationAction(moderator_id=user.id,action='fixture',target_type='review',target_id=str(i)))
            for i in range(31):
                p=Poll(question=f'Fixture poll {i}');p.options=[PollOption(label='Answer')]
                db.session.add(p);db.session.flush()
                if i==30:
                    self.poll_id=p.id;self.option_id=p.options[0].id
                    db.session.add_all(PollVote(poll_id=p.id,option_id=p.options[0].id,visitor_key=f'v{j}') for j in range(200))
            db.session.commit()

    def render(self,path):
        context={};loaded=[]
        def capture(sender,template,context,**kw):result.update(context)
        def load(session,obj):loaded.append(type(obj))
        result={};template_rendered.connect(capture,self.app);event.listen(Session,'loaded_as_persistent',load)
        try:r=self.client.get(path)
        finally:template_rendered.disconnect(capture,self.app);event.remove(Session,'loaded_as_persistent',load)
        self.assertEqual(r.status_code,200)
        self.assertIn('no-store',r.headers['Cache-Control'])
        self.assertIn(b'noindex, nofollow',r.data)
        return r,result,loaded

    def test_complete_histories_only_load_selected_queue(self):
        types={'reports':Report,'feedback':FeedbackSubmission,'audit':ModerationAction,'polls':Poll}
        for tab,total in (('reports',66),('feedback',67),('audit',67),('polls',31)):
            ids=[]
            for page in range(1,(total+24)//25+1):
                r,c,loaded=self.render(f'/moderation?tab={tab}&page={page}')
                self.assertEqual(c['pagination'].total,total)
                self.assertLessEqual(loaded.count(types[tab]),25)
                self.assertNotIn(PollVote,loaded)
                for other,kind in types.items():
                    if other!=tab:self.assertNotIn(kind,loaded)
                ids.extend(x.id for x in c['pagination'].items)
            self.assertEqual(len(set(ids)),total)
        _,c,_=self.render('/moderation?tab=polls')
        self.assertEqual(c['poll_results'][self.poll_id][0].votes,200)

    def test_filters_summary_safe_links_and_page_bounds(self):
        r,c,_=self.render('/moderation?tab=feedback&category=idea&state=new')
        self.assertEqual(c['pagination'].total,33)
        self.assertEqual(c['feedback_summary']['total'],67)
        self.assertEqual(c['feedback_summary']['average'],3)
        self.assertIn(b'category=idea',r.data)
        r,c,_=self.render('/moderation?tab=feedback&state=closed')
        self.assertEqual(c['pagination'].total,1)
        self.assertNotIn(b'/%2fexample.com',r.data)
        for tab,last in (('reports',3),('feedback',3),('audit',3),('polls',2)):
            for raw,expected in ((str(10**100),last),('-2',1),('bad',1)):
                _,c,_=self.render(f'/moderation?tab={tab}&page={raw}')
                self.assertEqual(c['pagination'].page,expected)
        _,c,_=self.render('/moderation?tab=reports&state=dismissed')
        self.assertEqual(c['pagination'].total,1)

    def test_access_csrf_actions_and_closed_report_guard(self):
        with self.app.app_context():
            fid=db.session.scalar(select(FeedbackSubmission.id))
            closed=db.session.scalar(select(Report.id).where(Report.status=='dismissed'))
        self.assertEqual(self.client.post(f'/moderation/feedback/{fid}/planned').status_code,400)
        r=self.client.post(f'/moderation/feedback/{fid}/planned',data={'csrf_token':self.token()})
        self.assertIn('tab=feedback',r.location)
        self.assertEqual(self.client.post(f'/moderation/reports/{closed}/remove',data={'csrf_token':self.token()}).status_code,409)
        for path in (f'/moderation/feedback/{10**100}/planned',f'/moderation/reports/{10**100}/dismiss'):
            self.assertEqual(self.client.post(path,data={'csrf_token':self.token()}).status_code,404)
        self.app.config['ADMIN_EMAIL']='someone-else@example.com'
        for tab in ('reports','feedback','polls','audit'):
            self.assertEqual(self.client.get('/moderation?tab='+tab).status_code,403)
        self.client.post('/logout',data={'csrf_token':self.token()})
        self.assertEqual(self.client.get('/moderation').status_code,302)

    def test_feedback_poll_and_report_numeric_inputs_cannot_overflow(self):
        self.client.get('/feedback')
        for value in ('9'*5000,'²','-1','0'):
            r=self.client.post('/feedback',data={'csrf_token':self.token(),'category':'bug','message':'Valid message text','rating':value})
            self.assertEqual(r.status_code,200)
            r=self.client.post(f'/feedback/polls/{self.poll_id}/vote',data={'csrf_token':self.token(),'option':value})
            self.assertEqual(r.status_code,302)
        for target in ('review','comment'):
            self.assertEqual(self.client.post('/reports',data={'csrf_token':self.token(),'target':target,
                'target_id':'9'*100,'reason':'spam'}).status_code,404)
        for path in (f'/reviews/{10**100}/delete',f'/reviews/{10**100}/comments',f'/comments/{10**100}/delete',f'/feedback/polls/{10**100}/vote'):
            self.assertEqual(self.client.post(path,data={'csrf_token':self.token()}).status_code,404)
