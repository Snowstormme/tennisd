"""Reviewed corrections are narrow, drift-guarded and reversible."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from tennisd import create_app, db
from tennisd.catalog_audit import match_snapshot, readonly_catalog_session
from tennisd.live_tennis import ensure_history_players, normalize_history_match, store_confirmed_result
from tennisd.models import Match, MatchStatistic, Player, PlayerExternalId, Review, User, utcnow
from tennisd.result_corrections import corrected_snapshot, correction_for_match, definitions, reviewed_correction


class ResultCorrectionTests(unittest.TestCase):
    key = "itf-sharm-2026-030-kirci-yuan"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "correction-tests-only",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.temp.name) / 'test.db'}"})
        self.context = self.app.app_context();self.context.push()
        self.definition = definitions()[self.key]
        before = self.definition["before"]
        winner = Player(**before["winner"])
        loser = Player(**before["loser"])
        db.session.add_all((winner, loser))
        self.match = Match(id=before["id"], provider=before["provider"], provider_id=before["provider_id"],
            tour=before["tour"], tournament=before["tournament"], week_start=date.fromisoformat(before["date"]),
            score=before["score"], winner=winner, loser=loser, status="finished", surface="Hard", level="I", round="R32")
        db.session.add(self.match)
        db.session.add_all((PlayerExternalId(player=winner, provider="livetennisapi", external_id="13031"),
            PlayerExternalId(player=loser, provider="livetennisapi", external_id="263")))
        user = User(username="reviewer", email="reviewer@example.test", display_name="Reviewer", password_hash="unused-test-value")
        self.review = Review(user=user, match=self.match, watched_on=date(2026,10,28), body="Keep my private note", is_public=False)
        db.session.add(self.review);db.session.commit()
        self.review_id = self.review.id

    def tearDown(self):
        db.session.remove();db.engine.dispose();self.context.pop();self.temp.cleanup()

    def test_preview_has_no_writes_and_matches_official_evidence(self):
        count = db.session.scalar(select(func.count(Player.id)));db.session.commit()
        with readonly_catalog_session():
            plan = reviewed_correction(self.key)
        self.assertFalse(plan["changed"])
        self.assertEqual(plan["after"]["loser"]["name"], "Yi Yuan")
        self.assertEqual(db.session.scalar(select(func.count(Player.id))), count)
        self.assertEqual(match_snapshot(self.match), self.definition["before"])
        self.assertIsNone(correction_for_match(self.match))

    def test_apply_is_idempotent_and_preserves_diary_and_source_mappings(self):
        result = reviewed_correction(self.key, operation="apply");db.session.commit()
        self.assertTrue(result["changed"])
        self.assertEqual(match_snapshot(self.match), corrected_snapshot(self.definition))
        self.assertEqual(db.session.get(Review,self.review_id).body, "Keep my private note")
        self.assertEqual(db.session.get(Review,self.review_id).match_id, self.definition["before"]["id"])
        self.assertEqual(db.session.scalar(select(PlayerExternalId.player_id).where(PlayerExternalId.provider=="livetennisapi", PlayerExternalId.external_id=="263")), "wta-206294")
        self.assertFalse(reviewed_correction(self.key, operation="apply")["changed"])
        page = self.app.test_client().get("/matches/lt-211673").get_data(as_text=True)
        self.assertIn("Yi Yuan", page)
        self.assertIn(self.definition["evidence"]["draw_url"], page)
        self.assertNotIn("awaiting a data review", page)

    def test_rollback_restores_original_fields_without_deleting_new_profile_or_diary(self):
        reviewed_correction(self.key, operation="apply");db.session.commit()
        reviewed_correction(self.key, operation="rollback");db.session.commit()
        self.assertEqual(match_snapshot(self.match), self.definition["before"])
        self.assertIsNotNone(db.session.get(Player, "itf-800526975"))
        self.assertEqual(db.session.get(Review,self.review_id).body, "Keep my private note")
        self.assertFalse(reviewed_correction(self.key, operation="rollback")["changed"])

    def test_changed_result_and_existing_official_identity_fail_closed(self):
        self.match.score = "7-6 6-4";db.session.commit()
        with self.assertRaisesRegex(ValueError, "changed since review"):
            reviewed_correction(self.key, operation="apply")
        db.session.rollback();self.match.score=self.definition["before"]["score"];db.session.commit()
        db.session.add(PlayerExternalId(provider="itf", external_id="800526975", player_id="atp-207989"));db.session.commit()
        with self.assertRaisesRegex(ValueError, "ITF ID"):
            reviewed_correction(self.key, operation="apply")
        db.session.rollback()
        self.assertEqual(match_snapshot(self.match), self.definition["before"])
        self.assertIsNone(db.session.get(Player,"itf-800526975"))

    def test_related_statistics_require_review_instead_of_silent_reassignment(self):
        db.session.add(MatchStatistic(match_id=self.match.id, player_id=self.match.loser_id, aces=4));db.session.commit()
        with self.assertRaisesRegex(ValueError,"additional review"):
            reviewed_correction(self.key,operation="apply")
        db.session.rollback()
        self.assertEqual(self.match.loser_id,"wta-206294")

    def test_later_bad_provider_response_cannot_undo_the_verified_identity(self):
        reviewed_correction(self.key,operation="apply");db.session.commit()
        raw={"id":211673,"tour":"atp","tier":"itf_m15","tournament":"M15 Sharm ElSheikh 15",
            "status":"completed","draw":"singles","outcome":"completed","winner":1,"scheduled_time":"2026-10-28T10:00:00Z",
            "players":{"p1":{"id":13031,"name":"Koray Kirci"},"p2":{"id":263,"name":"Yue Yuan"}},
            "score":{"games":[[6,6],[1,2]]}}
        row=normalize_history_match(raw);ensure_history_players([row]);db.session.flush()
        self.assertIsNone(store_confirmed_result(row,utcnow()));db.session.commit()
        self.assertEqual(self.match.loser_id,"itf-800526975")
        self.assertIsNotNone(correction_for_match(self.match))


class ExistingProfileCorrectionTests(unittest.TestCase):
    keys = ("us-open-2024-machac-fognini", "us-open-2024-humbert-monteiro")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "existing-correction-tests-only",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.temp.name) / 'test.db'}"})
        self.context = self.app.app_context(); self.context.push()
        self.matches, self.reviews = {}, {}
        user = User(username="historyreviewer", email="history@example.test", display_name="History", password_hash="unused-test-value")
        for key in self.keys:
            correction = definitions()[key]
            before = correction["before"]
            winner, loser = Player(**before["winner"]), Player(**before["loser"])
            target = Player(**correction["corrected_player"])
            match = Match(id=before["id"], provider=before["provider"], provider_id=before["provider_id"],
                tour=before["tour"], tournament=before["tournament"], status=before["status"],
                week_start=date.fromisoformat(before["date"]), score=before["score"],
                winner=winner, loser=loser, level="G", round="R128", surface="Hard")
            review = Review(user=user, match=match, watched_on=date(2024,8,27), is_public=False, body="Keep history diary")
            db.session.add_all((winner, loser, target, match, review,
                PlayerExternalId(player=loser, provider="tennis_api", external_id=key)))
            self.matches[key], self.reviews[key] = match, review
        db.session.commit()

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.context.pop(); self.temp.cleanup()

    def test_existing_profiles_preview_apply_and_rollback_preserve_ids_and_mappings(self):
        players = db.session.scalar(select(func.count(Player.id)))
        mappings = db.session.execute(select(PlayerExternalId.provider, PlayerExternalId.external_id, PlayerExternalId.player_id)).all()
        db.session.commit()
        for key in self.keys:
            with self.subTest(key=key):
                correction = definitions()[key]
                with readonly_catalog_session():
                    preview = reviewed_correction(key)
                self.assertFalse(preview["changed"])
                self.assertEqual(match_snapshot(self.matches[key]), correction["before"])
                self.assertTrue(reviewed_correction(key, operation="apply")["changed"])
                db.session.commit()
                self.assertEqual(match_snapshot(self.matches[key]), corrected_snapshot(correction))
                self.assertFalse(reviewed_correction(key, operation="apply")["changed"])
                self.assertEqual(self.reviews[key].match_id, correction["before"]["id"])
                self.assertEqual(self.reviews[key].body, "Keep history diary")
                page = self.app.test_client().get("/matches/" + correction["before"]["id"]).get_data(as_text=True)
                self.assertIn("official US Open draw", page)
                self.assertIn(correction["corrected_player"]["name"], page)
                self.assertNotIn("awaiting a data review", page)
                self.assertTrue(reviewed_correction(key, operation="rollback")["changed"])
                db.session.commit()
                self.assertEqual(match_snapshot(self.matches[key]), correction["before"])
                self.assertIsNone(correction_for_match(self.matches[key]))
                self.assertFalse(reviewed_correction(key, operation="rollback")["changed"])
        self.assertEqual(db.session.scalar(select(func.count(Player.id))), players)
        self.assertEqual(db.session.execute(select(PlayerExternalId.provider, PlayerExternalId.external_id, PlayerExternalId.player_id)).all(), mappings)

    def test_preview_and_apply_refuse_missing_or_changed_existing_target(self):
        key = self.keys[0]
        target = db.session.get(Player, definitions()[key]["corrected_player"]["id"])
        target.country = "BRA"; db.session.commit()
        for operation in ("preview", "apply"):
            with self.assertRaisesRegex(ValueError, "different profile"):
                reviewed_correction(key, operation=operation)
            db.session.rollback()
        db.session.delete(target); db.session.commit()
        for operation in ("preview", "apply"):
            with self.assertRaisesRegex(ValueError, "profile is missing"):
                reviewed_correction(key, operation=operation)
            db.session.rollback()
        self.assertEqual(match_snapshot(self.matches[key]), definitions()[key]["before"])

    def test_result_drift_and_unknown_correction_are_refused(self):
        key = self.keys[1]
        self.matches[key].week_start = date(2024,8,28); db.session.commit()
        with self.assertRaisesRegex(ValueError, "changed since review"):
            reviewed_correction(key, operation="apply")
        with self.assertRaisesRegex(ValueError, "Unknown reviewed correction"):
            reviewed_correction("arbitrary-match-id", operation="apply")
        db.session.rollback()

    def test_related_statistics_stop_existing_profile_correction(self):
        key = self.keys[0]
        match = self.matches[key]
        db.session.add(MatchStatistic(match_id=match.id, player_id=match.loser_id, aces=2)); db.session.commit()
        with self.assertRaisesRegex(ValueError, "additional review"):
            reviewed_correction(key, operation="apply")
        db.session.rollback()
        self.assertEqual(match_snapshot(match), definitions()[key]["before"])
