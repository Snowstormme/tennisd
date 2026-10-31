"""Regression cases from the catalog audit, using an isolated database."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from tennisd import create_app, db
from tennisd.live_tennis import display_score, fetch_matches, import_history_matches, normalize_match, sync_matches
from tennisd.models import LiveMatch, Match, Player, PlayerExternalId, Review, User
from test_live_tennis import Response, Session, fixture, history_fixture


class CatalogIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "catalog-test-only",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.temp.name) / 'test.db'}",
        })
        self.context = self.app.app_context()
        self.context.push()
        self.key = patch.dict(os.environ, {"LIVETENNISAPI_KEY": "test-only"})
        self.key.start()
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.key.stop()
        self.temp.cleanup()

    def test_null_points_are_not_published(self):
        for points in ([None, None], [None, "30"], ["", "40"], ["0", "0"]):
            self.assertEqual(display_score({"games": [[6, 6], [1, 2]], "points": points}), "6–1 6–2")
        page = self.app.test_client().get("/matches")
        self.assertNotIn(b"None", page.data)

    def test_absent_fixture_is_not_cancelled_and_404_is_not_a_result(self):
        row = fixture("upcoming", self.now + timedelta(hours=1))
        sync_matches("upcoming", session=Session([row]), now=self.now)
        saved = db.session.get(LiveMatch, "91234")
        checked_at = saved.synced_at
        sync_matches("upcoming", session=Session([]), now=self.now + timedelta(days=1))
        sync_matches("live", session=Session([]), now=self.now + timedelta(days=1))
        self.assertEqual(saved.status, "upcoming")
        self.assertEqual(saved.synced_at, checked_at)
        self.assertIsNone(saved.winner_side)

    def test_complete_looking_live_score_is_not_archived_without_provider_confirmation(self):
        row = fixture(starts_at=self.now - timedelta(hours=2))
        row["score"]["games"] = [[6, 6], [1, 2]]
        sync_matches("live", session=Session([row]), now=self.now)
        sync_matches("live", session=Session([]), now=self.now + timedelta(days=1))
        self.assertIsNone(db.session.get(Match, "lt-91234"))
        self.assertEqual(db.session.get(LiveMatch, "91234").status, "live")

    def test_conflicting_external_player_id_cannot_cross_tours(self):
        wrong = db.session.get(Player, "atp-207989")
        db.session.add(PlayerExternalId(player=wrong, provider="livetennisapi", external_id="101"))
        db.session.commit()
        row = history_fixture()
        row.update(tour="wta", tier="wta_500")
        row["players"]["p1"]["name"] = "A WTA Player"
        seen, written = import_history_matches("2026-10-01", "2026-10-31", session=Session([row]))
        self.assertEqual((seen, written), (1, 0))
        self.assertIsNone(db.session.get(Match, "lt-81234"))

    def test_provider_correction_preserves_the_diary_entry(self):
        row = history_fixture()
        import_history_matches("2026-10-01", "2026-10-31", session=Session([row]))
        user = User(username="audit", email="audit@example.test", display_name="Audit")
        user.set_password("test-only-long-password")
        review = Review(user=user, match_id="lt-81234", watched_on=self.now.date(), body="Keep this note")
        db.session.add(review)
        db.session.commit()
        review_id = review.id
        row["winner"] = 2
        row["score"]["games"] = [[1, 2], [6, 6]]
        import_history_matches("2026-10-01", "2026-10-31", session=Session([row]))
        match = db.session.get(Match, "lt-81234")
        self.assertEqual(match.winner.name, "Jannik Sinner")
        self.assertEqual(match.score, "6–1 6–2")
        self.assertEqual(db.session.get(Review, review_id).body, "Keep this note")

    def test_cross_tour_archive_row_is_quarantined_from_catalog_and_stats(self):
        winner = db.session.get(Player, "atp-207989")
        loser = db.session.scalar(select(Player).where(Player.tour == "WTA"))
        db.session.add(Match(
            id="cross-tour-audit", tour="ATP", tournament="Invalid Audit Event", level="A",
            surface="Hard", week_start=self.now.date(), round="F", score="6-1 6-2",
            winner=winner, loser=loser,
        ))
        db.session.commit()
        client = self.app.test_client()
        self.assertNotIn(b"Invalid Audit Event", client.get("/matches").data)
        self.assertNotIn(b"Invalid Audit Event", client.get(f"/players/{loser.id}").data)
        self.assertIn(b"awaiting a data review", client.get("/matches/cross-tour-audit").data)

    def test_en_dash_score_renders_as_sets_without_null_annotations(self):
        row = fixture(starts_at=self.now)
        row["score"]["games"] = [[6, 2], [1, 3]]
        row["score"]["points"] = [None, None]
        sync_matches("live", session=Session([row]), now=self.now)
        html = self.app.test_client().get("/matches?view=live").get_data(as_text=True)
        self.assertIn("SET 1", html)
        self.assertIn("SET 2", html)
        self.assertNotIn("None", html)
        self.assertIn("Updated", html)

    def test_unresolved_result_with_a_winner_is_rejected(self):
        row = history_fixture()
        row["outcome"] = "unresolved"
        self.assertEqual(import_history_matches("2026-10-01", "2026-10-31", session=Session([row])), (0, 0))

    def test_unknown_surface_and_malformed_participants_are_not_invented(self):
        row = fixture()
        row["surface"] = None
        self.assertEqual(normalize_match(row)["surface"], "Unknown")
        row["players"]["p1"] = "malformed"
        self.assertIsNone(normalize_match(row))

    def test_explicit_cancellation_holds_the_archive_without_deleting_reviews(self):
        row = history_fixture()
        row["scheduled_time"] = (self.now - timedelta(days=1)).isoformat()
        import_history_matches("2026-10-01", "2026-10-31", session=Session([row]))
        from tennisd.live_tennis import reconcile_live_matches
        reconcile_live_matches(session=Session([], {"81234": {"id": 81234, "status": "cancelled"}}), include_finished=True)
        saved = db.session.get(Match, "lt-81234")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.status, "cancelled")
        self.assertNotIn(b"/matches/lt-81234", self.app.test_client().get("/matches").data)

    def test_listing_pages_are_followed_but_stay_within_the_configured_budget(self):
        class PagedSession(Session):
            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                offset = kwargs["params"]["offset"]
                return Response([{"id": offset + index} for index in range(100 if offset == 0 else 1)])

        session = PagedSession([])
        self.assertEqual(len(fetch_matches("live", session)), 101)
        self.assertEqual([call[1]["params"]["offset"] for call in session.calls], [0, 100])
        with patch.dict(os.environ, {"LIVETENNISAPI_MAX_PAGES": "1"}):
            capped = PagedSession([])
            self.assertEqual(len(fetch_matches("live", capped)), 100)
            self.assertEqual(len(capped.calls), 1)


if __name__ == "__main__":
    unittest.main()
