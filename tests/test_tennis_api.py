import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from tennisd import create_app, db
from tennisd.models import (
    IngestionCursor,
    Match,
    MatchParticipant,
    Player,
    PlayerExternalId,
    Tournament,
    TournamentEdition,
)
from tennisd.tennis_api import PROVIDER, import_result, sync_recent_and_history


def result_row(match_id, winner, loser, tour="ATP", day="2026-10-02", tournament="Wimbledon"):
    return {
        "matchId": match_id,
        "date": f"{day}T14:00:00Z",
        "result": "6-4 6-4",
        "roundId": 12,
        "best_of": 5 if tour == "ATP" else 3,
        "player1": {"id": f"{match_id}-w", "name": winner, "countryAcr": "ESP"},
        "player2": {"id": f"{match_id}-l", "name": loser, "countryAcr": "ITA"},
        "tournament": {
            "id": f"{tour.lower()}-wimbledon",
            "name": tournament,
            "date": f"{day}T00:00:00Z",
            "courtId": 5,
            "rankId": 4,
            "countryAcr": "GBR",
        },
    }


class Response:
    def __init__(self, rows, has_next=False):
        self.rows = rows
        self.has_next = has_next

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": self.rows, "hasNextPage": self.has_next}


class Session:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "/atp/" in url:
            return Response([result_row("atp-2026-1", "Carlos Alcaraz", "Jannik Sinner")])
        return Response([result_row("wta-2026-1", "Iga Swiatek", "Aryna Sabalenka", "WTA")])


class TennisApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "tennis-api.db"
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

    def test_sync_imports_results_and_keeps_cursor(self):
        session = Session()
        with self.app.app_context(), patch.dict(os.environ, {"RAPIDAPI_TENNIS_KEY": "test-key"}):
            summary = sync_recent_and_history(
                session=session,
                today=date(2026, 10, 3),
                max_requests=4,
            )

            self.assertEqual(summary["requests"], 4)
            self.assertEqual(summary["seen"], 4)
            self.assertEqual(summary["written"], 2)
            self.assertEqual(
                len(db.session.scalars(select(Match).where(Match.provider == PROVIDER)).all()),
                2,
            )
            self.assertIsNotNone(db.session.scalar(select(Tournament).where(Tournament.name == "Wimbledon")))
            self.assertIsNotNone(db.session.scalar(select(TournamentEdition).where(TournamentEdition.season == 2026)))
            self.assertEqual(
                len(db.session.scalars(
                    select(PlayerExternalId).where(PlayerExternalId.provider == PROVIDER)
                ).all()),
                4,
            )
            self.assertEqual(
                len(db.session.scalars(
                    select(MatchParticipant).join(Match).where(Match.provider == PROVIDER)
                ).all()),
                4,
            )
            atp_cursor = db.session.get(IngestionCursor, (PROVIDER, "history_atp"))
            wta_cursor = db.session.get(IngestionCursor, (PROVIDER, "history_wta"))
            self.assertIn("2026-09-23", atp_cursor.value)
            self.assertIn("2026-09-23", wta_cursor.value)

            second = sync_recent_and_history(
                session=session,
                today=date(2026, 10, 3),
                max_requests=2,
            )
            self.assertEqual(second["written"], 0)

    def test_existing_live_result_prevents_historical_duplicate(self):
        with self.app.app_context():
            winner = Player(id="live-winner", tour="ATP", name="Carlos Alcaraz")
            loser = Player(id="live-loser", tour="ATP", name="Jannik Sinner")
            db.session.add_all((winner, loser))
            db.session.flush()
            db.session.add(Match(
                id="live-result-1",
                provider="livetennisapi",
                provider_id="livetennisapi:1",
                status="finished",
                tour="ATP",
                tournament="Wimbledon",
                level="G",
                surface="Grass",
                week_start=datetime(2026, 7, 12, tzinfo=timezone.utc).date(),
                scheduled_at=datetime(2026, 7, 12, 14, tzinfo=timezone.utc),
                completed_at=datetime(2026, 7, 12, 16, tzinfo=timezone.utc),
                round="F",
                winner=winner,
                loser=loser,
                score="6-4 6-4",
                best_of=5,
            ))
            db.session.commit()

            imported = import_result(
                "ATP",
                result_row("tennis-api-duplicate", "Carlos Alcaraz", "Jannik Sinner", day="2026-07-12"),
            )
            db.session.commit()

            self.assertFalse(imported)
            self.assertEqual(
                len(db.session.scalars(select(Match).where(Match.provider == PROVIDER)).all()),
                0,
            )
            self.assertEqual(
                len(db.session.scalars(
                    select(PlayerExternalId).where(PlayerExternalId.provider == PROVIDER)
                ).all()),
                2,
            )


if __name__ == "__main__":
    unittest.main()
