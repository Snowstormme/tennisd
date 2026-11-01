"""A reviewed group repairs either side atomically, with no account-data loss."""

import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from tennisd import create_app, db
from tennisd.catalog_audit import match_snapshot, readonly_catalog_session
from tennisd.models import IngestionRun, Match, MatchParticipant, Player, PlayerExternalId, Review, User
from tennisd.result_corrections import batches, corrected_snapshot, definitions, reviewed_batch


class ReviewedBatchTests(unittest.TestCase):
    batch = "fognini-2024-archive"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "batch-tests-only",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.temp.name) / 'test.db'}"})
        self.context = self.app.app_context(); self.context.push()
        self.keys = batches()[self.batch]
        self.matches, self.reviews, players = {}, {}, {}
        member = User(username="batchreviewer", email="batch@example.test", display_name="Batch", password_hash="test-only")
        for key in self.keys:
            row = definitions()[key]; before = row["before"]
            for data in (before["winner"], before["loser"], row["corrected_player"]):
                if data["id"] not in players:
                    players[data["id"]] = Player(**data)
                    db.session.add(players[data["id"]])
            match = Match(id=before["id"], provider=before["provider"], provider_id=before["provider_id"],
                tour=before["tour"], tournament=before["tournament"], status=before["status"],
                week_start=date.fromisoformat(before["date"]), score=before["score"],
                winner=players[before["winner"]["id"]], loser=players[before["loser"]["id"]],
                surface="Hard", round="R?", level="M")
            review = Review(user=member, match=match, watched_on=date(2026,11,1),
                is_public=False, body="PRIVATE_BATCH_NOTE " + key, rating_half=8)
            self.matches[key], self.reviews[key] = match, review
            db.session.add_all((match, review))
            db.session.add_all(MatchParticipant(**item) for item in row["related_before"]["participants"])
        db.session.add(PlayerExternalId(player=players["ta-1a2c875fb1604950"], provider="tennis_api", external_id="keep-original-binding"))
        db.session.commit()

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.context.pop(); self.temp.cleanup()

    def test_preview_is_read_only_and_identifies_three_winners_and_four_losers(self):
        with readonly_catalog_session():
            report = reviewed_batch(self.batch)
        self.assertEqual(report["changed"], 0)
        self.assertFalse(report["blocked"])
        self.assertEqual(len(report["results"]), 7)
        self.assertEqual(sum(definitions()[key]["corrected_side"] == "winner" for key in self.keys), 3)
        for key in self.keys:
            self.assertEqual(match_snapshot(self.matches[key]), definitions()[key]["before"])

    def test_apply_rollback_and_repeat_preserve_notes_mappings_scores_and_other_side(self):
        player_count = db.session.scalar(select(func.count(Player.id)))
        self.assertEqual(reviewed_batch(self.batch, operation="apply")["changed"], 7)
        db.session.commit()
        self.assertEqual(reviewed_batch(self.batch, operation="apply")["changed"], 0)
        for key in self.keys:
            correction = definitions()[key]
            self.assertEqual(match_snapshot(self.matches[key]), corrected_snapshot(correction))
            for original in correction["related_before"]["participants"]:
                participant = db.session.get(MatchParticipant, original["id"])
                wanted = original["player_id"]
                if wanted == correction["before"][correction["corrected_side"]]["id"]:
                    wanted = "atp-104926"
                self.assertEqual(participant.player_id, wanted)
                self.assertEqual(participant.side, original["side"])
                self.assertEqual(participant.is_winner, original["is_winner"])
            self.assertEqual(self.reviews[key].body, "PRIVATE_BATCH_NOTE " + key)
            self.assertEqual(self.reviews[key].match_id, correction["before"]["id"])
        self.assertEqual(db.session.scalar(select(func.count(Player.id))), player_count)
        self.assertEqual(db.session.scalar(select(PlayerExternalId.player_id).where(
            PlayerExternalId.external_id == "keep-original-binding")), "ta-1a2c875fb1604950")
        self.assertEqual(db.session.scalar(select(func.count(IngestionRun.id)).where(IngestionRun.provider == "catalog:curation")), 7)
        self.assertEqual(reviewed_batch(self.batch, operation="rollback")["changed"], 7)
        db.session.commit()
        self.assertEqual(reviewed_batch(self.batch, operation="rollback")["changed"], 0)
        for key in self.keys:
            self.assertEqual(match_snapshot(self.matches[key]), definitions()[key]["before"])
            for original in definitions()[key]["related_before"]["participants"]:
                self.assertEqual(db.session.get(MatchParticipant, original["id"]).player_id, original["player_id"])

    def test_late_failure_rolls_back_whole_group_even_if_caller_catches_and_commits(self):
        last = self.keys[-1]
        self.matches[last].score = "unreviewed score"; db.session.commit()
        with self.assertRaisesRegex(ValueError, "changed since review"):
            reviewed_batch(self.batch, operation="apply")
        db.session.commit()
        for key in self.keys[:-1]:
            self.assertEqual(match_snapshot(self.matches[key]), definitions()[key]["before"])
            for original in definitions()[key]["related_before"]["participants"]:
                self.assertEqual(db.session.get(MatchParticipant, original["id"]).player_id, original["player_id"])
        self.assertEqual(db.session.scalar(select(func.count(IngestionRun.id)).where(IngestionRun.provider == "catalog:curation")), 0)

    def test_successful_group_still_requires_outer_transaction_commit(self):
        reviewed_batch(self.batch, operation="apply")
        db.session.rollback()
        for key in self.keys:
            self.assertEqual(match_snapshot(self.matches[key]), definitions()[key]["before"])

    def test_late_rollback_failure_keeps_entire_corrected_group(self):
        reviewed_batch(self.batch, operation="apply"); db.session.commit()
        last = self.keys[-1]
        row = definitions()[last]["related_before"]["participants"][0]
        db.session.get(MatchParticipant, row["id"]).entry = "WC"; db.session.commit()
        with self.assertRaisesRegex(ValueError, "Related rows changed"):
            reviewed_batch(self.batch, operation="rollback")
        db.session.commit()
        for key in self.keys:
            self.assertEqual(match_snapshot(self.matches[key]), corrected_snapshot(definitions()[key]))
        self.assertEqual(db.session.scalar(select(func.count(IngestionRun.id)).where(IngestionRun.provider == "catalog:curation")), 7)

    def test_new_participant_refuses_changes(self):
        correction = definitions()[self.keys[-1]]
        db.session.add(MatchParticipant(match_id=correction["before"]["id"], side=3, player_id="atp-104926"))
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "Related rows changed"):
            reviewed_batch(self.batch, operation="apply")
        db.session.commit()
        for key in self.keys:
            self.assertEqual(match_snapshot(self.matches[key]), definitions()[key]["before"])

    def test_unknown_group_pending_writes_and_invalid_target_side_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Unknown or oversized"):
            reviewed_batch("arbitrary-scope", operation="apply")
        self.matches[self.keys[0]].score = "pending"
        with self.assertRaisesRegex(RuntimeError, "pending database changes"):
            reviewed_batch(self.batch, operation="apply")
        db.session.rollback()
        correction = deepcopy(definitions()[self.keys[0]])
        correction["corrected_side"] = "server"
        with self.assertRaisesRegex(ValueError, "winner or loser"):
            corrected_snapshot(correction)
        correction["corrected_side"] = "loser"
        correction["corrected_player"]["id"] = correction["before"]["winner"]["id"]
        with self.assertRaisesRegex(ValueError, "distinct players"):
            corrected_snapshot(correction)
