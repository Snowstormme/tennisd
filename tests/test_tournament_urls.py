import unittest
from datetime import date
from xml.etree import ElementTree
from sqlalchemy import select
from tennisd import db
from tennisd.models import Match,Player,TournamentSubscription
from tennisd.tournament_catalog import tournament_slug
from tennisd.tournament_urls import explicit_tournament_slug
import test_app as fixture


class TournamentURLTests(unittest.TestCase):
    tearDown=fixture.TennisdFlows.tearDown
    register=fixture.TennisdFlows.register
    token=fixture.TennisdFlows.token
    names=('Choice Open','Choice-Open')

    def setUp(self):
        fixture.TennisdFlows.setUp(self)
        with self.app.app_context():
            players=db.session.scalars(select(Player).where(Player.tour=='ATP').limit(2)).all()
            for index,name in enumerate((*self.names,'Choice Unique','Choice Hidden','東京','北京')):
                for i in range(37 if index<2 else 1):
                    db.session.add(Match(id=f'choice-{index}-{i}',tour='ATP',tournament=name,
                        surface='Hard',level='A',round='R128' if index==3 else 'R32',status='finished',
                        week_start=date(2024,1,1),winner_id=players[0].id,loser_id=players[1].id,score='6-4 6-4'))
            db.session.commit()

    def test_legacy_collision_requires_choice_and_selected_pages_stay_separate(self):
        r=self.client.get('/tournaments/atp/choice-open')
        self.assertEqual(r.status_code,300);self.assertIn('no-store',r.headers['Cache-Control'])
        head=self.client.head('/tournaments/atp/choice-open')
        self.assertEqual(head.status_code,300);self.assertEqual(head.data,b'')
        choice=self.client.get('/tournaments/atp/choice-open/2024')
        self.assertEqual(choice.status_code,300)
        for name in self.names:
            slug=explicit_tournament_slug(name)
            self.assertIn(slug.encode(),r.data)
            self.assertIn((slug+'/2024').encode(),choice.data)
            page=self.client.get('/tournaments/atp/'+slug)
            self.assertEqual(page.status_code,200)
            self.assertIn(b'37 recorded matches',page.data)
            self.assertIn((slug+'/2024').encode(),page.data)
            self.assertIn((slug+'?page=2').encode(),page.data)
            edition=self.client.get('/tournaments/atp/'+slug+'/2024?round=R32&page=2')
            self.assertEqual(edition.status_code,200)
            self.assertIn((slug+'/2024').encode(),edition.data)
        self.assertEqual(self.client.get('/tournaments/atp/choice-open--'+'0'*64).status_code,404)
        self.assertEqual(self.client.get('/tournaments/wta/choice-open').status_code,404)

    def test_ambiguous_post_cannot_toggle_an_arbitrary_subscription(self):
        self.register('alice')
        r=self.client.post('/tournaments/atp/choice-open/subscribe',data={'csrf_token':self.token()})
        self.assertEqual(r.status_code,409)
        with self.app.app_context():self.assertEqual(db.session.scalars(select(TournamentSubscription)).all(),[])
        for name in self.names:
            slug=explicit_tournament_slug(name)
            r=self.client.post('/tournaments/atp/'+slug+'/subscribe',data={'csrf_token':self.token()})
            self.assertEqual(r.status_code,302);self.assertIn(slug,r.location)
        with self.app.app_context():
            self.assertEqual(set(db.session.scalars(select(TournamentSubscription.tournament))),set(self.names))

    def test_sitemaps_have_unique_resolvable_variant_urls(self):
        for path in ('/sitemap-tournaments.xml','/sitemap-tournament-editions.xml'):
            r=self.client.get(path);self.assertEqual(r.status_code,200)
            urls=[node.text for node in ElementTree.fromstring(r.data).iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
            self.assertEqual(len(urls),len(set(urls)))
            selected=[url for url in urls if 'choice-open' in url]
            self.assertEqual(len(selected),2)
            for url in selected:self.assertEqual(self.client.get(url.replace('http://127.0.0.1:5000','')).status_code,200)
            self.assertNotIn(b'choice-hidden',r.data)

    def test_unique_old_urls_still_work_and_non_latin_names_have_addresses(self):
        self.assertEqual(self.client.get('/tournaments/atp/choice-unique').status_code,200)
        self.assertEqual(self.client.get('/tournaments/atp/choice-hidden').status_code,404)
        self.assertEqual(tournament_slug('東京'),'event')
        self.assertEqual(self.client.get('/tournaments/atp/event').status_code,300)
        for name in ('東京','北京'):
            self.assertEqual(self.client.get('/tournaments/atp/'+explicit_tournament_slug(name)).status_code,200)
