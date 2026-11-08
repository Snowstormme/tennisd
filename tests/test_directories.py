"""Directory pages stay bounded and recorded singles ranks remain truthful."""
import html
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from flask import template_rendered
from sqlalchemy import select
from tennisd import create_app, db
from tennisd.models import Match, Player, RankingSnapshot


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app({'TESTING':True,'SECRET_KEY':'directory-tests-only',
            'SQLALCHEMY_DATABASE_URI':f"sqlite:///{Path(self.temp.name)/'db'}"})
        self.client=self.app.test_client()
        with self.app.app_context():
            winner=db.session.get(Player,'atp-207989');loser=db.session.get(Player,'atp-206173')
            for i in range(44):
                db.session.add(Match(id=f'directory-{i:03}',tour='ATP',tournament='Directory Paging',level='A',
                    surface='Hard',week_start=date(2024,1,1),round='R128' if i==43 else 'R32',
                    status='finished',winner=winner,loser=loser,score='6-4 6-4'))
            for i in range(65):
                player=Player(id=f'dir-person-{i:03}',name='Paging Person',tour='ATP' if i<40 else 'WTA')
                db.session.add(RankingSnapshot(player=player,ranked_on=date(2024,1,1),ranking_type='singles',rank=7))
                db.session.add(RankingSnapshot(player=player,ranked_on=date(2024,1,1),ranking_type='doubles',rank=1))
            for i in range(3):
                player=Player(id=f'dir-edge-{i}',name=f'Ranking Edge {i}',tour='ATP')
                db.session.add(player)
                db.session.add(RankingSnapshot(player=player,ranked_on=date(2025,1,1),ranking_type='doubles',rank=1))
                if i != 1:db.session.add(RankingSnapshot(player=player,ranked_on=date(2025,2,1),ranking_type='singles',rank=0))
                if i==0:db.session.add(RankingSnapshot(player=player,ranked_on=date(2024,1,1),ranking_type='singles',rank=8))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():db.session.remove();db.engine.dispose()
        self.temp.cleanup()

    def render(self,url):
        captured={}
        def capture(sender,template,context,**kwargs):captured.update(context)
        template_rendered.connect(capture,self.app)
        try:response=self.client.get(url)
        finally:template_rendered.disconnect(capture,self.app)
        self.assertEqual(response.status_code,200)
        return response,captured

    def test_archive_ties_have_complete_stable_pages_and_hide_r128(self):
        for order in ('newest','oldest'):
            seen=[]
            for page,size in ((1,18),(2,18),(3,7)):
                _,c=self.render(f'/matches?q=Directory+Paging&order={order}&page={page}')
                p=c['pagination'];self.assertEqual(p.total,43);self.assertEqual(len(p.items),size)
                seen += [m.id for m in p.items]
            self.assertEqual(seen,[f'directory-{i:03}' for i in range(43)])

    def test_player_and_ranking_pages_do_not_duplicate_names_or_mixed_types(self):
        for view in ('players','rankings'):
            seen=[]
            for page,size in ((1,30),(2,30),(3,5)):
                _,c=self.render(f'/players?view={view}&q=Paging+Person&page={page}')
                p=c['pagination'];self.assertEqual(p.total,65);self.assertEqual(len(p.items),size)
                seen += [r.player_id if view=='rankings' else r.id for r in p.items]
                if view=='rankings':self.assertTrue(all(r.rank==7 and r.ranking_type=='singles' for r in p.items))
            self.assertEqual(seen,[f'dir-person-{i:03}' for i in range(65)])

    def test_only_latest_valid_singles_rank_is_used_for_directory_and_badges(self):
        response,c=self.render('/players?view=rankings&q=Ranking+Edge')
        self.assertEqual(c['pagination'].total,1)
        self.assertEqual([(r.player_id,r.rank) for r in c['pagination'].items],[('dir-edge-0',8)])
        self.assertNotIn(b'player-ranking-badge">#0',response.data)
        _,c=self.render('/players?q=Ranking+Edge')
        self.assertEqual([c['player_metrics'][f'dir-edge-{i}']['rank'] for i in range(3)],[8,None,None])

    def test_huge_negative_invalid_and_empty_page_requests_are_bounded(self):
        for base,last in (('/matches?q=Directory+Paging',3),('/players?q=Paging+Person',3),('/players?q=Paging+Person&view=rankings',3)):
            for value,expected in ((str(10**100),last),('-8',1),('bad',1)):
                _,c=self.render(base+'&page='+value);self.assertEqual(c['pagination'].page,expected)
        for base in ('/matches','/players','/players?view=rankings'):
            _,c=self.render(base+('&' if '?' in base else '?')+'q=NOT_PRESENT_zzzz&page='+str(10**100))
            self.assertEqual((c['pagination'].total,c['pagination'].page,c['pagination'].pages),(0,1,1))
        self.render('/matches?year='+'9'*5000)

    def test_next_links_preserve_filters_and_anchor(self):
        for path,expected in (
            ('/matches?q=Directory+Paging&tour=ATP&surface=Hard&year=2024&level=A&order=oldest',
                dict(q='Directory Paging',tour='ATP',surface='Hard',year='2024',level='A',order='oldest')),
            ('/players?view=rankings&q=Paging+Person&tour=ATP',dict(q='Paging Person',view='rankings',tour='ATP'))):
            response,_=self.render(path)
            links=re.findall(r'rel="next" href="([^"]+)"',response.get_data(as_text=True))
            self.assertEqual(len(links),2)
            for link in links:
                url=urlsplit(html.unescape(link));query=parse_qs(url.query)
                self.assertEqual(url.fragment,'results');self.assertEqual(query['page'],['2'])
                for key,value in expected.items():self.assertEqual(query[key],[value])

    def test_search_previews_have_deterministic_match_ties(self):
        _,c=self.render('/search?q=Directory+Paging')
        self.assertEqual([m.id for m in c['matches']],[f'directory-{i:03}' for i in range(9)])
