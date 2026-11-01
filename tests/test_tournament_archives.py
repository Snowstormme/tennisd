"""Tournament summaries stay complete while result pages stay bounded."""
import html
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from flask import template_rendered
from sqlalchemy import event
from sqlalchemy.orm import Session
from tennisd import create_app, db
from tennisd.models import LiveMatch, Match, Player
from tennisd.tournament_catalog import tournament_slug


class TournamentArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app({'TESTING':True,'SECRET_KEY':'tournament-tests-only',
            'SQLALCHEMY_DATABASE_URI':f"sqlite:///{Path(self.temp.name)/'db'}"})
        self.client=self.app.test_client();self.name='Archive Window Open'
        self.path='/tournaments/atp/'+tournament_slug(self.name)
        with self.app.app_context():
            self.a=db.session.get(Player,'atp-207989');self.b=db.session.get(Player,'atp-206173')
            for i in range(60):
                code='R32' if i<50 else ('RR','Q4','ZZ','BR','R16','QF','SF','F','F','')[i-50]
                self.add(f'edition-{i:03}',date(2024,1,i%20+1),code,swap=i%2==1)
            for year in range(2000,2020):self.add(f'old-{year}',date(year,1,1),'F')
            self.add('hidden-round',date(2024,1,1),'R128')
            self.add('hidden-self',date(2024,1,1),'F',self_match=True)
            self.add('hidden-cancelled',date(2024,1,1),'F',status='cancelled')
            self.add('other-event',date(2024,1,1),'F',name=self.name+' Qualifying')
            now=datetime.now(timezone.utc)
            for key,event,age in (('this-event',self.name.swapcase(),0),('another-event',self.name+' Qualifying',0),('old-event',self.name,3)):
                db.session.add(LiveMatch(provider_id=key,status='live',tour='ATP',tournament=event,surface='Hard',
                    round='R32',draw='singles',player1_name='Carlos Alcaraz',player2_name='Jannik Sinner',
                    synced_at=now-timedelta(days=age),starts_at=now))
            db.session.commit()

    def add(self,key,day,round_code,swap=False,self_match=False,status='finished',name=None):
        db.session.add(Match(id=key,tour='ATP',tournament=name or self.name,surface='Hard',level='A',
            week_start=day,round=round_code,status=status,winner=self.b if swap else self.a,
            loser=self.a if swap or self_match else self.b,score='6-4 6-4'))

    def tearDown(self):
        with self.app.app_context():db.session.remove();db.engine.dispose()
        self.temp.cleanup()

    def render(self,path):
        captured={};loaded=[]
        def context(sender,template,context,**kwargs):captured.update(context)
        def load(session,obj):
            if isinstance(obj,Match):loaded.append(obj.id)
        template_rendered.connect(context,self.app);event.listen(Session,'loaded_as_persistent',load)
        try:response=self.client.get(path)
        finally:template_rendered.disconnect(context,self.app);event.remove(Session,'loaded_as_persistent',load)
        self.assertEqual(response.status_code,200)
        return response,captured,loaded

    def test_tournament_pages_cover_exact_catalog_without_loading_all_finals(self):
        seen=[]
        for page,size in ((1,18),(2,18),(3,18),(4,18),(5,8)):
            response,c,loaded=self.render(self.path+f'?page={page}')
            self.assertEqual(c['total_matches'],80);self.assertEqual(len(c['matches']),size)
            self.assertEqual([m.provider_id for m in c['live_matches']],['this-event'])
            self.assertLessEqual(len(loaded),18)
            self.assertEqual([(x['player'].id,x['titles']) for x in c['champions']],[('atp-207989',21),('atp-206173',1)])
            seen += [m.id for m in c['matches']]
            self.assertNotIn(b'/matches/other-event',response.data)
            self.assertNotIn(b'/matches/hidden-',response.data)
        self.assertEqual(len(seen),len(set(seen)));self.assertEqual(len(seen),80)

    def test_all_seasons_are_reachable_not_only_latest_twelve(self):
        response,c,_=self.render(self.path)
        self.assertEqual(len(c['years']),21)
        self.assertIn((self.path+'/2000').encode(),response.data)
        self.assertIn(b'Browse all 21 seasons',response.data)
        self.assertEqual(self.client.get(self.path+'/2000').status_code,200)

    def test_edition_pages_include_every_round_without_false_single_champion(self):
        seen=[]
        for page in range(1,5):
            response,c,loaded=self.render(self.path+f'/2024?page={page}')
            self.assertEqual(c['total_matches'],60);self.assertIsNone(c['final'])
            self.assertLessEqual(len(loaded),18)
            self.assertIn(b'Multiple finals are recorded',response.data)
            self.assertIn('ZZ',c['round_counts']);self.assertIn('',c['round_counts'])
            seen += [m.id for group in c['rounds'].values() for m in group]
            self.assertEqual(len(seen),len(set(seen)))
        self.assertEqual(set(seen),{f'edition-{i:03}' for i in range(60)})

    def test_round_filter_counts_and_links_are_complete_and_stable(self):
        seen=[]
        for page,size in ((1,18),(2,18),(3,14)):
            response,c,_=self.render(self.path+f'/2024?round=R32&page={page}')
            self.assertEqual(c['match_page'].total,50);self.assertEqual(len(c['matches']),size)
            self.assertEqual(c['round_counts']['R32'],50)
            self.assertEqual(c['total_matches'],60)
            seen += [m.id for m in c['matches']]
            if page<3:
                links=re.findall(r'rel="next" href="([^"]+)"',response.get_data(as_text=True))
                self.assertEqual(len(links),2)
                self.assertTrue(all('round=R32' in html.unescape(link) and link.endswith('#results') for link in links))
        self.assertEqual(len(set(seen)),50)
        for code in ('RR','Q4','ZZ','__unavailable__'):
            response,c,_=self.render(self.path+'/2024?round='+code)
            self.assertEqual(len(c['matches']),1)
            self.assertIn(b'"@type": "CollectionPage"',response.data)
            self.assertNotIn(b'EventCompleted',response.data)

    def test_huge_invalid_pages_and_rounds_are_safe_and_missing_editions_404(self):
        for suffix,last in (('',5),('/2024',4),('/2024?round=R32',3)):
            for value,expected in ((str(10**100),last),('-1',1),('bad',1)):
                _,c,_=self.render(self.path+suffix+('&' if '?' in suffix else '?')+'page='+value)
                self.assertEqual(c['match_page'].page,expected)
        _,c,_=self.render(self.path+'/2024?round=invalid');self.assertEqual(c['selected_round'],'')
        for season in (1967,2023,10**100):self.assertEqual(self.client.get(self.path+'/'+str(season)).status_code,404)

    def test_single_final_remains_visible_on_any_page_and_r128_is_preserved(self):
        _,c,loaded=self.render(self.path+'/2019')
        self.assertEqual(c['final'].id,'old-2019');self.assertEqual(c['total_matches'],1)
        self.assertLessEqual(len(loaded),2)
        with self.app.app_context():self.assertIsNotNone(db.session.get(Match,'hidden-round'))
        self.assertEqual(self.client.get('/matches/hidden-round').status_code,200)
