"""Hide early public rounds while retaining permanent results and diaries."""
import tempfile
import unittest
from datetime import date
from pathlib import Path
from sqlalchemy import select
from tennisd import create_app, db
from tennisd.catalog_audit import catalog_cohort_report
from tennisd.identities import catalog_is_visible
from tennisd.models import Match, Player, Review, User
from tennisd.stats import player_statistics


class RoundVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "visibility-test", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.temp.name)/'db'}"})
        self.ctx = self.app.app_context(); self.ctx.push()
        a,b = Player(id="visibility-a",name="Visibility Alpha",tour="ATP"),Player(id="visibility-b",name="Visibility Beta",tour="ATP")
        self.player=a
        for rid,round_code in (("hidden-round","R128"),("shown-round","R64")):
            db.session.add(Match(id=rid,tour="ATP",tournament="Visibility Tournament",level="G",surface="Hard",round=round_code,
                week_start=date(2024,8,27),status="finished",winner=a,loser=b,score="6-4 6-4"))
        owner=User(username="visibilityowner",email="visibility@example.test",display_name="Owner",password_hash="test-only")
        db.session.add(Review(user=owner,match_id="hidden-round",watched_on=date(2024,8,27),body="KEEP_PRIVATE_DIARY",is_public=False))
        db.session.commit()

    def tearDown(self):
        db.session.remove();db.engine.dispose();self.ctx.pop();self.temp.cleanup()

    def test_public_queries_hide_r128_but_audit_and_database_retain_it(self):
        ids=db.session.scalars(select(Match.id).where(catalog_is_visible(),Match.tournament=="Visibility Tournament")).all()
        self.assertEqual(ids,["shown-round"])
        report=catalog_cohort_report(tournament="Visibility Tournament")
        self.assertEqual(report["counts"]["matches"],2)
        self.assertEqual(player_statistics(self.player)["wins"],1)
        self.assertIsNotNone(db.session.get(Match,"hidden-round"))
        self.assertEqual(db.session.scalar(select(Review).where(Review.match_id=="hidden-round")).body,"KEEP_PRIVATE_DIARY")

    def test_catalog_search_sitemap_and_direct_page_have_consistent_visibility(self):
        client=self.app.test_client()
        for url in ("/matches?q=Visibility", "/search?q=Visibility"):
            body=client.get(url).get_data(as_text=True)
            self.assertNotIn('/matches/hidden-round',body)
            self.assertIn('/matches/shown-round',body)
            self.assertIn('1/32',body)
        body=client.get('/sitemap-matches-1.xml').get_data(as_text=True)
        self.assertNotIn('/matches/hidden-round',body)
        direct=client.get('/matches/hidden-round')
        self.assertEqual(direct.status_code,200)
        self.assertIn('1/64',direct.get_data(as_text=True))
        self.assertIn('noindex',direct.get_data(as_text=True))
