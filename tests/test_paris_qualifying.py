"""The complete remaining qualifying group retains accounts and results."""
from collections import Counter
import unittest
from sqlalchemy import func, select
from tennisd import db
from tennisd.catalog_audit import match_snapshot, readonly_catalog_session
from tennisd.models import IngestionRun
from tennisd.result_corrections import definitions, reviewed_batch
import test_round_recovery as fixture


class ParisQualifyingTests(unittest.TestCase):
    batch = 'paris-2024-qualifying-rounds'
    setUp = fixture.RoundRecoveryTests.setUp
    tearDown = fixture.RoundRecoveryTests.tearDown

    def test_preview_apply_repeat_and_rollback(self):
        with readonly_catalog_session():
            report = reviewed_batch(self.batch)
        self.assertFalse(report['blocked'])
        self.assertEqual(len(report['results']), 19)
        self.assertEqual(Counter(definitions()[k]['corrected_round'] for k in self.keys), {'Q1':13,'Q2':6})
        original = {k: match_snapshot(m) for k,m in self.matches.items()}
        self.assertEqual(reviewed_batch(self.batch, operation='apply')['changed'],19)
        db.session.commit()
        self.assertEqual(reviewed_batch(self.batch, operation='apply')['changed'],0)
        for k,m in self.matches.items():
            self.assertEqual(match_snapshot(m),original[k])
            self.assertEqual(m.round,definitions()[k]['corrected_round'])
            self.assertEqual(self.reviews[k].body,'ROUND_PRIVATE_NOTE '+k)
        self.assertEqual(reviewed_batch(self.batch, operation='rollback')['changed'],19)
        db.session.commit()
        for k,m in self.matches.items():
            self.assertEqual(match_snapshot(m,include_round=True),definitions()[k]['before'])

    def test_late_drift_cannot_leave_partial_repairs(self):
        self.matches[self.keys[-1]].round='F';db.session.commit()
        with self.assertRaisesRegex(ValueError,'changed since review'):
            reviewed_batch(self.batch,operation='apply')
        db.session.commit()
        for k in self.keys[:-1]:
            self.assertEqual(self.matches[k].round,definitions()[k]['before']['round'])
        self.assertEqual(db.session.scalar(select(func.count(IngestionRun.id))),0)
