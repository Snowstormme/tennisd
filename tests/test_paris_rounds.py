"""The maximum-size reviewed group must preserve results and diary entries."""
import unittest
from sqlalchemy import func, select
from tennisd import db
from tennisd.catalog_audit import match_snapshot, readonly_catalog_session
from tennisd.models import IngestionRun
from tennisd.result_corrections import definitions, reviewed_batch
import test_round_recovery as round_fixture


class ParisRoundTests(unittest.TestCase):
    batch = "paris-2024-main-r64"
    setUp = round_fixture.RoundRecoveryTests.setUp
    tearDown = round_fixture.RoundRecoveryTests.tearDown

    def test_twenty_reviewed_results_preview_apply_repeat_and_rollback(self):
        with readonly_catalog_session():
            report = reviewed_batch(self.batch)
        self.assertEqual(len(report["results"]), 20)
        self.assertFalse(report["blocked"])
        self.assertEqual(report["changed"], 0)
        self.assertEqual(reviewed_batch(self.batch, operation="apply")["changed"], 20)
        db.session.commit()
        self.assertEqual(reviewed_batch(self.batch, operation="apply")["changed"], 0)
        for key in self.keys:
            original = definitions()[key]["before"].copy(); original.pop("round")
            self.assertEqual(match_snapshot(self.matches[key]), original)
            self.assertEqual(self.matches[key].round, "R64")
            self.assertEqual(self.reviews[key].body, "ROUND_PRIVATE_NOTE " + key)
        self.assertEqual(reviewed_batch(self.batch, operation="rollback")["changed"], 20)
        db.session.commit()
        for key in self.keys:
            self.assertEqual(match_snapshot(self.matches[key], include_round=True), definitions()[key]["before"])

    def test_last_record_drift_rolls_back_all_twenty_and_their_journals(self):
        self.matches[self.keys[-1]].round = "F"; db.session.commit()
        with self.assertRaisesRegex(ValueError, "changed since review"):
            reviewed_batch(self.batch, operation="apply")
        db.session.commit()
        for key in self.keys[:-1]:
            self.assertEqual(self.matches[key].round, "R128")
        self.assertEqual(db.session.scalar(select(func.count(IngestionRun.id))), 0)

    def test_source_positions_identify_twenty_distinct_opening_pairs(self):
        positions = [tuple(definitions()[key]["evidence"]["draw_positions"]) for key in self.keys]
        self.assertEqual(len(set(positions)), 20)
        self.assertTrue(all(a % 2 == 1 and b == a + 1 and 1 <= a < b <= 64 for a,b in positions))
