"""Search previews must lead to complete, literal and private-safe categories."""
import html
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from flask import template_rendered
from sqlalchemy import event
from sqlalchemy.orm import Session
from tennisd import create_app, db
from tennisd.models import Match, Player, User


class SearchResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app({'TESTING':True,'SECRET_KEY':'search-tests-only',
            'SQLALCHEMY_DATABASE_URI':f"sqlite:///{Path(self.temp.name)/'db'}"})
        self.client=self.app.test_client()
        with self.app.app_context():
            a=db.session.get(Player,'atp-207989');b=db.session.get(Player,'atp-206173')
            for i in range(33):
                db.session.add(Player(id=f'search-player-{i:03}',name='Search Needle',tour='ATP'))
                db.session.add(User(username=f'searchmember{i:03}',display_name='Search Needle',
                    email=f'PRIVATE_EMAIL_{i}@example.test',password_hash='PRIVATE_PASSWORD_HASH'))
            for i in range(45):
                db.session.add(Match(id=f'search-match-{i:03}',winner=a,loser=b,tour='ATP',tournament=f'Search Needle Cup {i%35:02}',
                    surface='Hard',level='A',round='R32',status='finished',week_start=date(2024,1,1),score='6-4 6-4'))
            for key,round_code,self_match in (('hidden-search-round','R128',False),('hidden-search-self','R32',True)):
                db.session.add(Match(id=key,winner=a,loser=a if self_match else b,tour='ATP',tournament='Search Needle HIDDEN',
                    surface='Hard',level='A',round=round_code,status='finished',week_start=date(2024,1,1),score='6-4 6-4'))
            literal=Player(id='search-literal',name='Literal_50%! Ace',tour='ATP')
            db.session.add(Match(id='literal-match',winner=literal,loser=b,tour='ATP',tournament='Literal_50%! Open',surface='Hard',
                level='A',round='F',status='finished',week_start=date(2024,1,1),score='6-4 6-4'))
            db.session.add(User(username='literal_member',display_name='Literal_50%! Fan',email='literal@example.test',password_hash='unused'))
            db.session.add(Player(id='search-control',name='LiteralX50A! Ace',tour='ATP'))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():db.session.remove();db.engine.dispose()
        self.temp.cleanup()

    def render(self,path):
        captured={};loaded=[]
        def context(sender,template,context,**kw):captured.update(context)
        def load(session,obj):
            if isinstance(obj,Match):loaded.append(obj.id)
        template_rendered.connect(context,self.app);event.listen(Session,'loaded_as_persistent',load)
        try:response=self.client.get(path)
        finally:template_rendered.disconnect(context,self.app);event.remove(Session,'loaded_as_persistent',load)
        self.assertEqual(response.status_code,200)
        return response,captured,loaded

    def test_overview_discloses_limits_and_complete_counts_without_private_fields(self):
        response,c,loaded=self.render('/search?q=Search+Needle')
        self.assertEqual(c['totals'],dict(players=33,matches=45,tournaments=35,members=33))
        self.assertEqual([len(c['results'][key]) for key in c['categories']],[15,9,12,15])
        self.assertEqual(len(loaded),9)
        self.assertIn(b'Showing 9 of 45',response.data)
        self.assertEqual(response.data.count(b'View all '),4)
        self.assertNotIn(b'PRIVATE_EMAIL',response.data);self.assertNotIn(b'PRIVATE_PASSWORD_HASH',response.data)
        self.assertNotIn(b'Search Needle HIDDEN',response.data)
        self.assertIn(b'noindex, follow',response.data)

    def test_every_category_is_complete_with_bounded_stable_pages(self):
        for category,total,size in (('players',33,30),('matches',45,18),('tournaments',35,30),('members',33,30)):
            seen=[]
            for page in range(1,(total+size-1)//size+1):
                response,c,loaded=self.render(f'/search?q=Search+Needle&category={category}&page={page}')
                self.assertEqual(c['pagination'].total,total)
                self.assertLessEqual(len(loaded),18)
                self.assertEqual(len(c['results'][category]),min(size,total-len(seen)))
                for key,items in c['results'].items():
                    if key!=category:self.assertEqual(items,[])
                seen += [(item.tour,item.tournament) if category=='tournaments' else item.id for item in c['results'][category]]
                self.assertNotIn(b'PRIVATE_EMAIL',response.data)
            self.assertEqual(len(seen),len(set(seen)));self.assertEqual(len(seen),total)

    def test_huge_invalid_empty_queries_and_pages_are_safe(self):
        for category,last in (('players',2),('matches',3),('tournaments',2),('members',2)):
            for value,expected in ((str(10**100),last),('-4',1),('bad',1)):
                _,c,_=self.render(f'/search?q=Search+Needle&category={category}&page={value}')
                self.assertEqual(c['pagination'].page,expected)
            _,c,_=self.render(f'/search?q=NOT_FOUND_zzzz&category={category}&page='+str(10**100))
            self.assertEqual((c['pagination'].page,c['pagination'].total),(1,0))
        _,c,_=self.render('/search?category=members');self.assertIsNone(c['pagination'])
        _,c,_=self.render('/search?q=Search+Needle&category=invalid');self.assertEqual(c['category'],'all')
        _,c,_=self.render('/search?q='+'x'*100);self.assertEqual(len(c['query']),80)

    def test_sql_wildcards_and_escape_are_literal_in_all_search_surfaces(self):
        query=quote('Literal_50%!')
        _,c,_=self.render('/search?q='+query)
        self.assertEqual(c['totals'],dict.fromkeys(c['categories'],1))
        for path,key in (('/players?q=','pagination'),('/matches?q=','pagination')):
            _,c,_=self.render(path+query);self.assertEqual(c[key].total,1)
        _,c,_=self.render('/tournaments?q='+query);self.assertEqual(c['tournament_total'],1)
        for symbol in ('%','_','!'):
            _,c,_=self.render('/search?q='+quote(symbol))
            self.assertEqual(c['totals']['players'],2 if symbol=='!' else 1)

    def test_next_links_and_search_form_preserve_category_and_query(self):
        response,c,_=self.render('/search?q=Search+Needle&category=matches')
        links=re.findall(r'rel="next" href="([^"]+)"',response.get_data(as_text=True));self.assertEqual(len(links),2)
        for link in links:
            url=urlsplit(html.unescape(link));self.assertEqual(url.fragment,'results')
            self.assertEqual(parse_qs(url.query),dict(q=['Search Needle'],category=['matches'],page=['2']))
        self.assertIn(b'name="category" value="matches"',response.data)
        response,_,_=self.render('/search?q='+quote('<script>alert(1)</script>'))
        self.assertNotIn(b'<script>alert(1)</script>',response.data)
        self.assertIn(b'&lt;script&gt;alert(1)',response.data)

    def test_distinct_tournament_rows_keep_both_tours_without_duplicates(self):
        with self.app.app_context():
            aw=db.session.get(Player,'atp-207989');al=db.session.get(Player,'atp-206173')
            ww=Player(id='search-wta-w',name='WTA Winner',tour='WTA');wl=Player(id='search-wta-l',name='WTA Loser',tour='WTA')
            for i,(winner,loser,tour) in enumerate(((aw,al,'ATP'),(aw,al,'ATP'),(ww,wl,'WTA'))):
                db.session.add(Match(id=f'twin-{i}',winner=winner,loser=loser,tour=tour,tournament='Distinct Twin',surface='Hard',
                    level='A',round='F',status='finished',week_start=date(2024,1,1),score='6-4 6-4'))
            db.session.commit()
        _,c,_=self.render('/search?q=Distinct+Twin&category=tournaments')
        self.assertEqual(c['pagination'].total,2)
        self.assertEqual([(r.tour,r.tournament) for r in c['tournaments_found']],[('ATP','Distinct Twin'),('WTA','Distinct Twin')])
