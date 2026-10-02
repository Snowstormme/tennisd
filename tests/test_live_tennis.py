import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tennisd import create_app, db
from tennisd.live_tennis import display_score, normalize_match, sync_matches, winner_from_score
from tennisd.models import LiveMatch, Match, Player, PlayerExternalId
from sqlalchemy import select


def fixture(status="live", starts_at=None):
    return {
        "id": 91234,
        "tournament": "Wimbledon",
        "tournament_id": "wimbledon-wta-singles",
        "tier": "grand_slam",
        "tour": "wta",
        "surface": "grass",
        "round": "Wimbledon - Quarter-finals",
        "round_code": "QF",
        "draw": "singles",
        "is_doubles": False,
        "status": status,
        "scheduled_time": (starts_at or datetime.now(timezone.utc)).isoformat(),
        "players": {
            "p1": {"id": 1, "name": "Iga Swiatek"},
            "p2": {"id": 2, "name": "Aryna Sabalenka"},
        },
        "score": {
            "games": [[6, 3, 2], [4, 6, 1]],
            "points": ["30", "15"], "server": 1,
            "timestamp": "2026-07-08T13:20:00Z",
        },
    }


class Response:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": self.rows}


class Session:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.rows)


class LiveTennisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "live.db"
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-only-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database}",
        })
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temporary.cleanup()

    def test_score_and_featured_tier_filtering(self):
        now = datetime(2026, 7, 8, 13, tzinfo=timezone.utc)
        row = fixture(starts_at=now)
        self.assertEqual(display_score(row["score"]), "6–4 3–6 2–1 (30–15)")
        normalized = normalize_match(row, now)
        self.assertEqual(normalized["tournament"], "Wimbledon")
        self.assertEqual(normalized["tour"], "WTA")
        row["tournament"] = "Berlin Open"
        row["tier"] = "wta_500"
        self.assertEqual(normalize_match(row, now)["tournament"], "Berlin Open")
        row["tier"] = "wta_250"
        self.assertEqual(normalize_match(row, now)["tier"], "wta_250")

    def test_upcoming_window_and_sync(self):
        now = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
        inside = fixture("upcoming", now + timedelta(days=3))
        outside = fixture("upcoming", now + timedelta(days=8))
        outside["id"] = 99999
        session = Session([inside, outside])
        with self.app.app_context(), patch.dict(os.environ, {"LIVETENNISAPI_KEY": "test-key"}):
            self.assertEqual(sync_matches("upcoming", session=session, now=now), 1)
            match = db.session.get(LiveMatch, "91234")
            self.assertEqual(match.player1_name, "Iga Swiatek")
            self.assertEqual(match.surface, "Grass")
        tiers = session.calls[0][1]["params"]["tier"].split(",")
        self.assertIn("grand_slam", tiers)
        self.assertIn("atp_250", tiers)
        self.assertIn("challenger_100", tiers)
        self.assertIn("itf_w35", tiers)
        self.assertNotIn("draw", session.calls[0][1]["params"])

    def test_matches_page_and_internal_score_endpoint(self):
        with self.app.app_context():
            db.session.add(LiveMatch(
                provider_id="live-1", status="live", tour="ATP",
                tournament="US Open", surface="Hard", round="SF",
                starts_at=datetime.now(timezone.utc), player1_name="Carlos Alcaraz",
                player2_name="Jannik Sinner", score="6–4 2–3", server=2,
            ))
            db.session.commit()
        page = self.client.get("/matches?view=live")
        self.assertIn(b"Live on court", page.data)
        self.assertIn(b"surface-hard", page.data)
        self.assertIn(b"Carlos Alcaraz", page.data)
        payload = self.client.get("/api/live-matches").get_json()
        self.assertEqual(payload["matches"][0]["score"], "6\u20134 2\u20133")

    def test_sync_creates_permanent_profiles_for_new_live_players(self):
        now = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
        row = fixture("upcoming", now + timedelta(days=2))
        row["players"] = {
            "p1": {"id": 88001, "name": "New Tour Player"},
            "p2": {"id": 88002, "name": "Second Tour Player"},
        }
        with self.app.app_context(), patch.dict(os.environ, {"LIVETENNISAPI_KEY": "test-key"}):
            self.assertEqual(sync_matches("upcoming", session=Session([row]), now=now), 1)
            first = db.session.scalar(select(Player).where(Player.name == "New Tour Player"))
            second = db.session.scalar(select(Player).where(Player.name == "Second Tour Player"))
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertEqual(first.tour, "WTA")
            self.assertEqual(
                db.session.scalar(select(PlayerExternalId.player_id).where(
                    PlayerExternalId.provider == "livetennisapi",
                    PlayerExternalId.external_id == "88001",
                )),
                first.id,
            )
            self.assertEqual(sync_matches("upcoming", session=Session([row]), now=now), 1)
            self.assertEqual(
                len(db.session.scalars(select(Player).where(Player.name == "New Tour Player")).all()),
                1,
            )
            first_id = first.id

        detail = self.client.get("/live-matches/91234")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(f'/players/{first_id}'.encode(), detail.data)
        self.assertIn(b"View player profile", detail.data)
        profile = self.client.get(f"/players/{first_id}")
        self.assertEqual(profile.status_code, 200)
        self.assertIn(b"CURRENT TOUR FEED", profile.data)
        self.assertIn(b"Second Tour Player", profile.data)

    def test_finished_live_match_is_preserved_and_has_a_page(self):
        now = datetime(2026, 7, 8, 16, tzinfo=timezone.utc)
        with self.app.app_context():
            db.session.add(LiveMatch(
                provider_id="settled-1", status="live", tour="WTA", tournament="Wimbledon",
                surface="Grass", round="F", starts_at=now - timedelta(hours=2),
                player1_name="Iga Swiatek", player2_name="Aryna Sabalenka", score="6–4 6–3",
            ))
            db.session.commit()
            with patch.dict(os.environ, {"LIVETENNISAPI_KEY": "test-key"}):
                self.assertEqual(sync_matches("live", session=Session([]), now=now), 0)
            match = db.session.get(LiveMatch, "settled-1")
            self.assertEqual(match.status, "finished")
            self.assertEqual(match.winner_side, 1)
            archived = db.session.scalar(select(Match).where(Match.provider == "livetennisapi"))
            self.assertIsNotNone(archived)
            self.assertEqual(archived.score, "6–4 6–3")
        page = self.client.get("/live-matches/settled-1")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"PERMANENT", self.client.get("/matches?view=finished").data)

    def test_winner_from_final_score(self):
        self.assertEqual(winner_from_score("6–4 3–6 7–5"), 1)


if __name__ == "__main__":
    unittest.main()
