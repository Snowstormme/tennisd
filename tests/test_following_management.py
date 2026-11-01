import unittest
from flask import template_rendered
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from tennisd import db
from tennisd.models import FollowedPlayer, Player, TournamentSubscription, User
import test_app as fixture


class FollowingManagementTests(unittest.TestCase):
    tearDown=fixture.TennisdFlows.tearDown
    register=fixture.TennisdFlows.register
    token=fixture.TennisdFlows.token

    def setUp(self):
        fixture.TennisdFlows.setUp(self);self.register('alice')
        with self.app.app_context():
            self.uid=db.session.scalar(select(User.id).where(User.username=='alice'))
            other=User(username='other',email='other@example.test',display_name='Other',password_hash='unused')
            db.session.add(other);db.session.flush();self.other=other.id
            self.ids={'players':[],'tournaments':[]}
            for i in range(67):
                player=Player(id=f'follow-player-{i}',tour='ATP' if i%2==0 else 'WTA',
                    name='Following %_!' if i==0 else f'Following Player {i}')
                db.session.add(player);db.session.flush()
                follow=FollowedPlayer(user_id=self.uid,player_id=player.id)
                subscription=TournamentSubscription(user_id=self.uid,tour=player.tour,
                    tournament='Following %_!' if i==0 else f'Following Event {i}')
                db.session.add_all((follow,subscription));db.session.flush()
                self.ids['players'].append(follow.id);self.ids['tournaments'].append(subscription.id)
            other_follow=FollowedPlayer(user_id=self.other,player_id='follow-player-0')
            other_tournament=TournamentSubscription(user_id=self.other,tour='ATP',tournament='Private other tournament')
            db.session.add_all((other_follow,other_tournament));db.session.commit()
            self.foreign={'players':other_follow.id,'tournaments':other_tournament.id}

    def render(self,path,client=None):
        context={};loaded=[]
        def capture(sender,template,context,**kw):result.update(context)
        def load(session,obj):loaded.append(type(obj))
        result={};template_rendered.connect(capture,self.app);event.listen(Session,'loaded_as_persistent',load)
        try:r=(client or self.client).get(path)
        finally:template_rendered.disconnect(capture,self.app);event.remove(Session,'loaded_as_persistent',load)
        self.assertEqual(r.status_code,200)
        return r,result,loaded

    def test_complete_private_lists_and_selected_loading(self):
        for tab,model,other in (('players',FollowedPlayer,TournamentSubscription),('tournaments',TournamentSubscription,FollowedPlayer)):
            seen=[]
            for page,size in ((1,30),(2,30),(3,7)):
                r,c,loaded=self.render(f'/settings/following?tab={tab}&page={page}')
                self.assertEqual(c['totals'],{'players':67,'tournaments':67})
                self.assertEqual(c['pagination'].total,67)
                self.assertEqual(len(c['pagination'].items),size)
                self.assertLessEqual(loaded.count(model),30);self.assertNotIn(other,loaded)
                self.assertIn('no-store',r.headers['Cache-Control']);self.assertIn(b'noindex, nofollow',r.data)
                seen.extend(x.id for x in c['pagination'].items)
            self.assertEqual(seen,list(reversed(self.ids[tab])))

    def test_literal_filters_page_bounds_and_complete_export(self):
        for tab in self.ids:
            _,c,_=self.render(f'/settings/following?tab={tab}&q=%25_!&tour=ATP')
            self.assertEqual(c['pagination'].total,1)
            for raw,expected in ((str(10**100),3),('bad',1),('-1',1)):
                _,c,_=self.render(f'/settings/following?tab={tab}&page={raw}')
                self.assertEqual(c['pagination'].page,expected)
            _,c,_=self.render(f'/settings/following?tab={tab}&tour=WTA')
            self.assertEqual(c['pagination'].total,33)
        data=self.client.get('/settings/export').get_json()
        self.assertEqual(len(data['followed_players']),67);self.assertEqual(len(data['followed_tournaments']),67)
        _,c,_=self.render('/settings/following?tab=invalid&tour=invalid&q=no_match_zzzz')
        self.assertEqual((c['tab'],c['tour'],c['pagination'].total),('players','',0))

    def test_explicit_owner_removal_csrf_and_stale_links(self):
        for tab,model in (('players',FollowedPlayer),('tournaments',TournamentSubscription)):
            url=f'/settings/following/{tab}/{self.ids[tab][0]}/remove'
            self.assertEqual(self.client.post(url).status_code,400)
            self.assertEqual(self.client.post(f'/settings/following/{tab}/{self.foreign[tab]}/remove',
                data={'csrf_token':self.token()}).status_code,404)
            r=self.client.post(url,data={'csrf_token':self.token(),'q':'%_!','tour':'ATP','page':str(10**100),'next':'https://example.com'})
            self.assertEqual(r.status_code,302);self.assertTrue(r.location.startswith('/settings/following?'))
            _,c,_=self.render(r.location)
            self.assertEqual((c['query'],c['tour'],c['pagination'].page,c['pagination'].total),('%_!','ATP',1,0))
            self.assertEqual(self.client.post(url,data={'csrf_token':self.token()}).status_code,404)
            self.assertEqual(self.client.post(f'/settings/following/{tab}/{10**100}/remove',data={'csrf_token':self.token()}).status_code,404)
            with self.app.app_context():
                self.assertIsNone(db.session.get(model,self.ids[tab][0]))
                self.assertIsNotNone(db.session.get(model,self.foreign[tab]))

    def test_overview_is_bounded_and_other_profiles_do_not_expose_follows(self):
        r,c,loaded=self.render('/u/alice')
        self.assertEqual((len(c['followed']),len(c['tournament_subscriptions'])),(12,12))
        self.assertEqual(c['following_totals'],{'players':67,'tournaments':67})
        self.assertIn(b'Manage all 67',r.data)
        guest=self.app.test_client()
        r,c,_=self.render('/u/alice',guest)
        self.assertEqual(c['followed'],[]);self.assertEqual(c['tournament_subscriptions'],[])
        self.assertNotIn(b'Following Event',r.data)
        self.assertEqual(guest.get('/settings/following').status_code,302)
        self.assertIn(b'Manage subscriptions',self.client.get('/settings').data)
