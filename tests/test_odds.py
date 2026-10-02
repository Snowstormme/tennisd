import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from tennisd import create_app, db
from tennisd.models import LiveMatch, LiveOdds
from tennisd.odds import implied_probabilities, selected_sport_key, sync_odds


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(next(self.payloads))


class OddsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "odds.db"
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-only-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database}",
        })

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temporary.cleanup()

    def test_probability_margin_is_removed_and_rotation_is_deterministic(self):
        first, second = implied_probabilities(1.80, 2.10)
        self.assertAlmostEqual(first + second, 100.0)
        now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        self.assertEqual(selected_sport_key(["a", "b", "c"], now), selected_sport_key(["a", "b", "c"], now))

    def test_sync_matches_names_and_renders_quote(self):
        now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        with self.app.app_context():
            db.session.add(LiveMatch(
                provider_id="live-odds-1", status="live", tour="ATP", tournament="Shanghai Masters",
                surface="Hard", round="R16", starts_at=now,
                player1_name="Carlos Alcaraz", player2_name="Jannik Sinner", score="2–1",
            ))
            db.session.commit()
            session = Session([
                [{"key": "tennis_atp_shanghai", "group": "Tennis", "active": True}],
                [{
                    "id": "odds-event-1", "home_team": "Jannik Sinner", "away_team": "Carlos Alcaraz",
                    "bookmakers": [{"key": "pinnacle", "last_update": "2026-10-02T11:55:00Z", "markets": [{
                        "key": "h2h", "outcomes": [
                            {"name": "Jannik Sinner", "price": 2.20},
                            {"name": "Carlos Alcaraz", "price": 1.72},
                        ],
                    }]}],
                }],
            ])
            with patch.dict(os.environ, {"THE_ODDS_API_KEY": "test-key"}):
                result = sync_odds(session=session, now=now)
            self.assertEqual(result["written"], 1)
            self.assertEqual(len(session.calls), 2)
            quote = db.session.scalar(select(LiveOdds))
            self.assertEqual(quote.player1_price, 1.72)
            self.assertEqual(quote.player2_price, 2.20)
            self.assertAlmostEqual(quote.player1_probability + quote.player2_probability, 100.0)

        page = self.app.test_client().get("/matches?view=live")
        self.assertIn(b"FULL MATCH", page.data)
        self.assertIn(b"1.72", page.data)
