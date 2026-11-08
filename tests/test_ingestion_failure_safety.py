import unittest
from datetime import datetime, timezone
from unittest.mock import patch
import requests
from sqlalchemy import func, select
from tennisd import db
from tennisd.live_tennis import import_history_matches, reconcile_live_matches, sync_matches
from tennisd.models import IngestionCursor, LiveMatch, Match
from tennisd.result_corrections import reviewed_correction
import test_result_corrections as fixture


class IngestionSafetyTests(unittest.TestCase):
    key=fixture.ResultCorrectionTests.key
    setUp=fixture.ResultCorrectionTests.setUp
    tearDown=fixture.ResultCorrectionTests.tearDown
    now=datetime(2026,10,29,tzinfo=timezone.utc)

    def live(self,identifier):
        row=LiveMatch(provider_id=identifier,status='live',tour='ATP',tournament='Fixture',
            surface='Hard',round='R32',starts_at=datetime(2026,10,28,tzinfo=timezone.utc),
            synced_at=datetime(2026,10,28,tzinfo=timezone.utc),player1_name='A',player2_name='B',is_doubles=False)
        db.session.add(row)
        return row

    def test_cancelled_and_unverified_observations_preserve_reviewed_archive(self):
        reviewed_correction(self.key,operation='apply');db.session.commit()
        observation=self.live('211673');db.session.commit()
        before={c.key:getattr(self.match,c.key) for c in Match.__table__.columns}
        for status,expected in (('cancelled','cancelled'),('unknown','verifying')):
            observation.status='live';db.session.commit()
            with patch('tennisd.live_tennis.fetch_match_detail',return_value={'id':211673,'status':status}):
                self.assertEqual(reconcile_live_matches(now=self.now),0)
            self.assertEqual(observation.status,expected)
            self.assertEqual({c.key:getattr(self.match,c.key) for c in Match.__table__.columns},before)

    def test_late_provider_failure_cannot_be_accidentally_committed_and_retry_succeeds(self):
        rows=[self.live(key) for key in ('first','second')]
        control=Match(id='unreviewed-first',provider_id='livetennisapi:first',tour='ATP',tournament='Fixture',
            surface='Hard',level='I',round='R32',week_start=self.match.week_start,
            winner=self.match.winner,loser=self.match.loser,score='6-1 6-2',status='finished')
        db.session.add(control);db.session.commit()
        with patch('tennisd.live_tennis.fetch_match_detail',side_effect=[{'status':'cancelled'},requests.Timeout('fixture')]):
            with self.assertRaises(requests.Timeout):reconcile_live_matches(now=self.now)
        db.session.commit()  # A caller catches the error and continues.
        self.assertEqual([r.status for r in rows],['live','live'])
        self.assertEqual(control.status,'finished')
        self.assertEqual(db.session.scalar(select(func.count(IngestionCursor.feed))),0)
        with patch('tennisd.live_tennis.fetch_match_detail',return_value={'status':'cancelled'}):
            reconcile_live_matches(now=self.now)
        self.assertEqual([r.status for r in rows],['cancelled','cancelled'])
        self.assertEqual(control.status,'cancelled')
        self.assertEqual(db.session.scalar(select(func.count(IngestionCursor.feed))),2)

    def test_history_storage_failure_rolls_back_profiles_observations_and_archive(self):
        def fail(rows):
            self.match.score='PARTIAL WRITE'
            db.session.flush()
            raise ValueError('fixture storage failure')
        with patch('tennisd.live_tennis.fetch_history_matches',return_value=[]),patch('tennisd.live_tennis.ensure_history_players',side_effect=fail):
            with self.assertRaises(ValueError):import_history_matches('2026-10-28','2026-10-29',now=self.now)
        db.session.commit()
        self.assertEqual(self.match.score,self.definition['before']['score'])

    def test_live_listing_failure_rolls_back_its_pending_rows(self):
        row={'provider_id':'new-fixture','status':'live','tour':'ATP','tournament':'Fixture',
            'surface':'Hard','round':'R32',
            'player1_name':'A','player2_name':'B','synced_at':self.now,'is_doubles':False}
        with patch('tennisd.live_tennis.fetch_matches',return_value=[{}]),patch('tennisd.live_tennis.normalize_match',return_value=row),patch('tennisd.live_tennis.ensure_live_player_profiles',side_effect=ValueError('fixture')):
            with self.assertRaises(ValueError):sync_matches('upcoming',now=self.now)
        db.session.commit()
        self.assertIsNone(db.session.get(LiveMatch,'new-fixture'))

    def test_successful_listing_remains_committed_if_followup_reconciliation_fails(self):
        row={'provider_id':'committed-fixture','status':'live','tour':'ATP','tournament':'Fixture',
            'surface':'Hard','round':'R32','player1_name':'A','player2_name':'B','synced_at':self.now,'is_doubles':False}
        with patch('tennisd.live_tennis.fetch_matches',return_value=[{}]),patch('tennisd.live_tennis.normalize_match',return_value=row),patch('tennisd.live_tennis.ensure_live_player_profiles'),patch('tennisd.live_tennis.reconcile_live_matches',side_effect=requests.Timeout('fixture')):
            with self.assertRaises(requests.Timeout):sync_matches('live',now=self.now)
        db.session.commit()
        self.assertIsNotNone(db.session.get(LiveMatch,'committed-fixture'))
