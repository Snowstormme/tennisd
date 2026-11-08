"""Two bounded groups complete the reviewed Paris main-draw rounds."""
import unittest
from sqlalchemy import func, select
from tennisd import db
from tennisd.catalog_audit import match_snapshot, readonly_catalog_session
from tennisd.models import IngestionRun
from tennisd.result_corrections import definitions, reviewed_batch
import test_round_recovery as fixture


class MainRoundChecks:
    setUp = fixture.RoundRecoveryTests.setUp
    tearDown = fixture.RoundRecoveryTests.tearDown

    def test_apply_repeat_rollback_preserve_results_and_private_entries(self):
        with readonly_catalog_session():
            preview=reviewed_batch(self.batch)
        self.assertFalse(preview['blocked'])
        self.assertEqual(len(preview['results']),self.count)
        original={k:match_snapshot(m) for k,m in self.matches.items()}
        self.assertEqual(reviewed_batch(self.batch,operation='apply')['changed'],self.count)
        db.session.commit()
        self.assertEqual(reviewed_batch(self.batch,operation='apply')['changed'],0)
        for k,m in self.matches.items():
            self.assertEqual(match_snapshot(m),original[k])
            self.assertEqual(m.round,definitions()[k]['corrected_round'])
            self.assertEqual(self.reviews[k].body,'ROUND_PRIVATE_NOTE '+k)
            a,b=definitions()[k]['evidence']['draw_positions']
            self.assertEqual(b-a,{'R64':1,'R32':3,'R16':7}[m.round])
        self.assertEqual(reviewed_batch(self.batch,operation='rollback')['changed'],self.count)
        db.session.commit()
        for k,m in self.matches.items():
            self.assertEqual(match_snapshot(m,include_round=True),definitions()[k]['before'])

    def test_last_record_drift_rolls_back_complete_group(self):
        self.matches[self.keys[-1]].round='F';db.session.commit()
        with self.assertRaisesRegex(ValueError,'changed since review'):
            reviewed_batch(self.batch,operation='apply')
        db.session.commit()
        for k in self.keys[:-1]:
            self.assertEqual(self.matches[k].round,definitions()[k]['before']['round'])
        self.assertEqual(db.session.scalar(select(func.count(IngestionRun.id))),0)


class EarlyMainRounds(MainRoundChecks,unittest.TestCase):
    batch='paris-2024-main-remaining-early'
    count=19


class LastSixteenRounds(MainRoundChecks,unittest.TestCase):
    batch='paris-2024-main-r16'
    count=8
