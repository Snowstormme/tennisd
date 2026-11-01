"""Tournament cards stay bounded while counts, filters and metadata stay complete."""
import unittest
from datetime import date
from unittest.mock import patch
from sqlalchemy import event
from sqlalchemy.orm import Session
from tennisd import db
from tennisd.models import Match
from tennisd.tournament_catalog import tournament_profile
import test_directories as fixture


class TournamentDirectoryTests(unittest.TestCase):
    tearDown=fixture.DirectoryTests.tearDown
    render=fixture.DirectoryTests.render

    def setUp(self):
        fixture.DirectoryTests.setUp(self)
        with self.app.app_context():
            sample=db.session.get(Match,'directory-000')
            for i in range(80):
                for j,surface in enumerate(('Hard','Clay')):
                    db.session.add(Match(id=f'event-{i}-{j}',tour='ATP',tournament=f'Directory Event {i:03}',
                        surface=surface,level='A',week_start=date(2025,1,1),round='R32',
                        winner_id=sample.winner_id,loser_id=sample.loser_id,score='6-4 6-4'))
            for i in range(12):
                db.session.add(Match(id=f'major-{i}',tour='ATP',tournament=f'Directory Major {i:03}',
                    surface='Hard',level='G',week_start=date(2025,1,1),round='R64',
                    winner_id=sample.winner_id,loser_id=sample.loser_id,score='6-4 6-4'))
            for key,name,round_code in (('literal','Directory Literal %_!','R32'),('hidden','Directory Hidden','R128')):
                db.session.add(Match(id=key,tour='ATP',tournament=name,surface='Hard',level='A',
                    week_start=date(2025,1,1),round=round_code,winner_id=sample.winner_id,
                    loser_id=sample.loser_id,score='6-4 6-4'))
            db.session.commit()

    def test_all_tied_events_have_stable_pages_and_complete_metadata(self):
        seen=[]
        for page,size in ((1,36),(2,36),(3,8)):
            response,c=self.render(f'/tournaments?q=Directory+Event&page={page}')
            self.assertEqual(c['pagination'].total,80)
            self.assertEqual(len(c['tournaments']),size)
            for item in c['tournaments']:
                self.assertEqual(item['matches'],2)
                self.assertEqual(item['surfaces'],{'Hard','Clay'})
                seen.append(item['tournament'])
            self.assertEqual(response.data.count(b'rel="next"'),2 if page<3 else 0)
            self.assertIn(b'q=Directory+Event',response.data)
            self.assertIn(b'#results',response.data)
        self.assertEqual(seen,[f'Directory Event {i:03}' for i in range(80)])

    def test_first_page_major_preview_is_bounded_and_all_majors_are_reachable(self):
        loaded=[]
        def load(session,obj):
            if isinstance(obj,Match):loaded.append(obj.id)
        event.listen(Session,'loaded_as_persistent',load)
        try:
            with patch('tennisd.tournament_directory.tournament_profile',wraps=tournament_profile) as profiles:
                response,c=self.render('/tournaments')
                self.assertLessEqual(profiles.call_count,44)
        finally:event.remove(Session,'loaded_as_persistent',load)
        self.assertEqual(loaded,[])
        self.assertEqual(len(c['featured']),8)
        self.assertGreaterEqual(c['featured_count'],12)
        self.assertIn(b'Browse all',response.data)
        self.assertIn(b'level=G',response.data)
        _,next_page=self.render('/tournaments?page=2')
        self.assertEqual(next_page['featured'],[])
        _,majors=self.render('/tournaments?level=G&q=Directory+Major')
        self.assertEqual(majors['pagination'].total,12)

    def test_filters_count_only_matching_results_and_preserve_navigation(self):
        response,c=self.render('/tournaments?q=Directory+Event&surface=Clay&tour=ATP&level=A')
        self.assertEqual(c['pagination'].total,80)
        self.assertTrue(all(x['matches']==1 and x['surfaces']=={'Clay'} for x in c['tournaments']))
        for value in (b'surface=Clay',b'tour=ATP',b'level=A'):self.assertIn(value,response.data)
        _,c=self.render('/tournaments?q=%25_!')
        self.assertEqual([x['tournament'] for x in c['tournaments']],['Directory Literal %_!'])
        _,c=self.render('/tournaments?q=Directory+Hidden')
        self.assertEqual(c['pagination'].total,0)

    def test_huge_invalid_negative_and_empty_pages_are_bounded(self):
        for value,expected in ((str(10**100),3),('bad',1),('-9',1)):
            _,c=self.render('/tournaments?q=Directory+Event&page='+value)
            self.assertEqual(c['pagination'].page,expected)
        _,c=self.render('/tournaments?q=no_event_zzzz&page='+str(10**100))
        self.assertEqual((c['pagination'].total,c['pagination'].pages),(0,1))
        _,c=self.render('/tournaments?tour=invalid&surface=invalid&level=invalid')
        self.assertEqual((c['tour'],c['surface'],c['level']),('','',''))
