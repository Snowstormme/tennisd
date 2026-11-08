"""Whole-history summaries and bounded identity-specific archive pages."""
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import event
from tennisd import create_app, db
from tennisd.models import Match, Player, RankingSnapshot
from tennisd.stats import player_statistics


class PlayerSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "player-stats-tests",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.temp.name)/'db'}"})
        self.ctx = self.app.app_context(); self.ctx.push()
        self.prizes = patch("tennisd.routes.update_prize_money"); self.prizes.start()
        self.player = Player(id="stats-player", name="Summary Player", tour="ATP")
        self.others = [Player(id="stats-a", name="Alex Same", tour="ATP"), Player(id="stats-b", name="Alex Same", tour="ATP")]
        self.empty = Player(id="stats-empty", name="Empty Player", tour="ATP")
        namesake = Player(id="stats-namesake", name="Summary Player", tour="ATP")
        wrong = Player(id="stats-wrong", name="WTA Player", tour="WTA")
        db.session.add_all([self.player, *self.others, self.empty, namesake, wrong])
        for i in range(60):
            won = i % 3 != 2
            row = Match(id=f"player-fixture-{i:03}", winner=self.player if won else self.others[i%2],
                loser=self.others[i%2] if won else self.player, tour="ATP", tournament="Summary Open", surface="Hard",
                level="G" if i%2 == 0 else "A", round="F" if i<21 else "R32", status="finished",
                week_start=date(2024,1,1), score="6-4 6-4")
            prefix = "w" if won else "l"
            samples = [(0,50,100,25,1,2,25),(4,None,1000,None,None,100,None),
                       (2,30,60,20,3,4,10),(-1,110,100,120,5,4,None),(None,None,None,None,None,None,0)]
            if i<len(samples):
                for name, value in zip(("ace","first_in","svpt","first_won","bp_saved","bp_faced"),samples[i][:6]):
                    setattr(row,prefix+"_"+name,value)
                setattr(row,"winner_rank" if won else "loser_rank",samples[i][6])
            db.session.add(row)
        for identifier, winner, loser, round_code, status in (
            ("hidden",self.player,self.others[0],"R128","finished"),
            ("cross",self.player,wrong,"F","finished"),
            ("self",self.player,self.player,"F","finished"),
            ("pending",self.player,self.others[0],"F","cancelled"),
            ("namesake",namesake,self.others[0],"F","finished"),
        ):
            db.session.add(Match(id="player-"+identifier,winner=winner,loser=loser,tour="ATP",tournament="Summary Open",
                surface="Hard",level="G",round=round_code,status=status,week_start=date(2025,1,1),score="6-0 6-0",w_ace=1000))
        db.session.add_all([RankingSnapshot(player=self.player,ranked_on=date(2024,1,1),rank=4,points=0,ranking_type="singles"),
            RankingSnapshot(player=self.player,ranked_on=date(2024,1,2),rank=0,points=999,ranking_type="singles")])
        db.session.commit()

    def tearDown(self):
        self.prizes.stop(); db.session.remove(); db.engine.dispose(); self.ctx.pop(); self.temp.cleanup()

    def test_summary_covers_all_visible_matches_and_bounds_loaded_titles(self):
        db.session.expunge_all()
        loads=[]
        def loaded(row, context): loads.append(row.id)
        event.listen(Match,"load",loaded)
        try:
            summary=player_statistics(db.session.get(Player,"stats-player"))
        finally:
            event.remove(Match,"load",loaded)
        self.assertEqual((summary["match_count"],summary["wins"],summary["losses"]),(60,40,20))
        self.assertEqual((summary["title_count"],summary["slam_titles"]),(14,7))
        self.assertEqual(len(summary["titles"]),12)
        self.assertLessEqual(len(loads),12)
        self.assertEqual(summary["surfaces"],{"Hard":{"wins":40,"losses":20}})
        self.assertEqual(summary["seasons"],[(2024,{"wins":40,"losses":20})])
        self.assertEqual([x["player"].id for x in summary["head_to_head"]],["stats-a","stats-b"])
        self.assertTrue(all((x["wins"],x["losses"])==(20,10) for x in summary["head_to_head"]))

    def test_rates_use_paired_valid_counts_and_known_zero_aces(self):
        summary=player_statistics(self.player)
        self.assertEqual(summary["aces_per_match"],2.0)
        self.assertEqual(summary["ace_matches"],3)
        self.assertEqual(summary["first_serve_pct"],50)
        self.assertEqual(summary["first_serve_won_pct"],56)
        self.assertEqual(summary["break_saved_pct"],67)
        self.assertTrue(all(summary[key]==2 for key in ("first_serve_matches","first_serve_won_matches","break_saved_matches")))
        self.assertEqual(summary["recent_rank"]["rank"],10)

    def test_pages_cover_whole_player_id_history_once_with_stable_ties(self):
        client=self.app.test_client(); ids=[]
        for page in range(1,5):
            response=client.get(f"/players/stats-player?page={page}")
            self.assertEqual(response.status_code,200)
            body=response.get_data(as_text=True)
            section=body.split('id="match-history"',1)[1]
            rows=re.findall(r'href="/matches/(player-fixture-\d+)"',section)
            self.assertLessEqual(len(rows),18); ids.extend(rows)
            self.assertIn("60 visible matches",body)
            self.assertIn("Latest 12 of 14",body)
            self.assertNotIn("/matches/player-hidden",body)
            self.assertNotIn("/matches/player-namesake",body)
        self.assertEqual(ids,[f"player-fixture-{i:03}" for i in reversed(range(60))])

    def test_huge_and_invalid_pages_empty_profile_and_rank_zero(self):
        client=self.app.test_client()
        body=client.get('/players/stats-player?page='+str(10**100)).get_data(as_text=True)
        self.assertIn("Page 4 of 4",body)
        self.assertIn("BEST IN LOADED HISTORY #4",body)
        self.assertIn("0 points",body)
        self.assertIn("Page 1 of 4",client.get('/players/stats-player?page=bad').get_data(as_text=True))
        empty=player_statistics(self.empty)
        self.assertEqual(empty["match_count"],0)
        self.assertIsNone(empty["first_serve_pct"])
        self.assertEqual(empty["ace_matches"],0)
        self.assertEqual(client.get('/players/stats-empty?page=999999').status_code,200)

    def test_profile_hydrates_only_page_and_latest_twelve_titles(self):
        db.session.expunge_all(); loads=[]
        def loaded(row, context): loads.append(row.id)
        event.listen(Match,"load",loaded)
        try:
            response=self.app.test_client().get('/players/stats-player?page=2')
        finally:
            event.remove(Match,"load",loaded)
        self.assertEqual(response.status_code,200)
        self.assertLessEqual(len(loads),30)

    def test_known_zero_percentages_are_not_treated_as_missing(self):
        db.session.add(Match(id="known-zero-service",winner=self.empty,loser=self.others[0],tour="ATP",
            tournament="Zero Open",surface="Clay",level="A",round="R32",status="finished",week_start=date(2024,1,1),
            score="6-4 6-4",w_ace=0,w_first_in=0,w_svpt=50,w_first_won=0,w_bp_saved=0,w_bp_faced=2))
        db.session.commit()
        summary=player_statistics(self.empty)
        self.assertEqual(summary["aces_per_match"],0.0)
        self.assertEqual(summary["first_serve_pct"],0)
        self.assertEqual(summary["break_saved_pct"],0)
        self.assertIsNone(summary["first_serve_won_pct"])
