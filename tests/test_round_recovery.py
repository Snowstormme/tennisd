"""Round-only recovery keeps participants/results and private diaries intact."""

import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from tennisd import create_app, db
from tennisd.catalog_audit import match_snapshot, readonly_catalog_session
from tennisd.models import IngestionRun, Match, MatchParticipant, Player, Review, User
from tennisd.result_corrections import batches, corrected_snapshot, correction_for_match, definitions, reviewed_batch


class RoundRecoveryTests(unittest.TestCase):
    batch = "fognini-2024-rounds"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "round-tests-only",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.temp.name) / 'test.db'}"})
        self.context = self.app.app_context(); self.context.push()
        self.keys = batches()[self.batch]
        self.matches, self.reviews, players = {}, {}, {}
        user = User(username="roundreviewer", email="round-private@example.test", display_name="Rounds", password_hash="test-only")
        for key in self.keys:
            correction = definitions()[key]; before = correction["before"]
            for data in (before["winner"], before["loser"]):
                if data["id"] not in players:
                    players[data["id"]] = Player(**data)
                    db.session.add(players[data["id"]])
            match = Match(id=before["id"], provider=before["provider"], provider_id=before["provider_id"],
                tour=before["tour"], tournament=before["tournament"], status=before["status"],
                week_start=date.fromisoformat(before["date"]), score=before["score"], round=before["round"],
                winner=players[before["winner"]["id"]], loser=players[before["loser"]["id"]], surface="Hard", level="M")
            review = Review(user=user, match=match, watched_on=date(2026,11,1), is_public=False, body="ROUND_PRIVATE_NOTE " + key)
            self.matches[key], self.reviews[key] = match, review
            db.session.add_all((match, review))
            db.session.add_all(MatchParticipant(**row) for row in correction["related_before"]["participants"])
        db.session.commit()

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.context.pop(); self.temp.cleanup()

    def test_readonly_preview_exposes_only_five_reviewed_round_changes(self):
        with readonly_catalog_session():
            report = reviewed_batch(self.batch)
        self.assertEqual(len(report["results"]), 5)
        self.assertEqual(report["changed"], 0)
        self.assertFalse(report["blocked"])
        for result in report["results"]:
            before, after = deepcopy(result["before"]), deepcopy(result["after"])
            self.assertNotEqual(before.pop("round"), after.pop("round"))
            self.assertEqual(before, after)
            self.assertEqual(result["related_before"], result["related_after"])
        for key in self.keys:
            self.assertEqual(self.matches[key].round, definitions()[key]["before"]["round"])

    def test_apply_repeat_and_rollback_preserve_all_other_result_fields_and_notes(self):
        before = {key: match_snapshot(match) for key, match in self.matches.items()}
        self.assertEqual(reviewed_batch(self.batch, operation="apply")["changed"], 5); db.session.commit()
        self.assertEqual(reviewed_batch(self.batch, operation="apply")["changed"], 0)
        for key in self.keys:
            correction = definitions()[key]; match = self.matches[key]
            self.assertEqual(match_snapshot(match), before[key])
            self.assertEqual(match.round, correction["corrected_round"])
            self.assertEqual(correction_for_match(match)["kind"], "round")
            self.assertEqual(self.reviews[key].body, "ROUND_PRIVATE_NOTE " + key)
            self.assertEqual(self.reviews[key].match_id, match.id)
            for row in correction["related_before"]["participants"]:
                actual = db.session.get(MatchParticipant, row["id"])
                self.assertEqual({column.name: getattr(actual, column.name) for column in actual.__table__.columns}, row)
        page = self.app.test_client().get("/matches/ta-atp-1105613").get_data(as_text=True)
        self.assertIn("Player identity and round verified", page)
        self.assertIn("Qualifying 2", page)
        self.assertEqual(reviewed_batch(self.batch, operation="rollback")["changed"], 5); db.session.commit()
        self.assertEqual(reviewed_batch(self.batch, operation="rollback")["changed"], 0)
        for key in self.keys:
            self.assertEqual(match_snapshot(self.matches[key], include_round=True), definitions()[key]["before"])
            # The independently verified participant correction still applies.
            self.assertNotEqual(correction_for_match(self.matches[key]).get("kind"), "round")

    def test_late_unreviewed_round_cancels_entire_group_and_journals(self):
        last = self.keys[-1]
        self.matches[last].round = "F"; db.session.commit()
        with self.assertRaisesRegex(ValueError, "changed since review"):
            reviewed_batch(self.batch, operation="apply")
        db.session.commit()
        for key in self.keys[:-1]:
            self.assertEqual(self.matches[key].round, definitions()[key]["before"]["round"])
        self.assertEqual(db.session.scalar(select(func.count(IngestionRun.id))), 0)

    def test_participant_drift_and_caller_rollback_cannot_leave_partial_round_changes(self):
        reviewed_batch(self.batch, operation="apply"); db.session.rollback()
        for key in self.keys:
            self.assertEqual(self.matches[key].round, definitions()[key]["before"]["round"])
        correction = definitions()[self.keys[-1]]
        db.session.get(MatchParticipant, correction["related_before"]["participants"][0]["id"]).seed = "3"
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "Related rows changed"):
            reviewed_batch(self.batch, operation="apply")
        db.session.commit()
        for key in self.keys:
            self.assertEqual(self.matches[key].round, definitions()[key]["before"]["round"])

    def test_unsupported_target_round_is_refused(self):
        correction = deepcopy(definitions()[self.keys[0]])
        correction["corrected_round"] = "Q4"
        with self.assertRaisesRegex(ValueError, "target round"):
            corrected_snapshot(correction)
