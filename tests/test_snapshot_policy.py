"""A visibility cutoff must never fabricate a match outcome or notification."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import select
from tennisd import create_app, db
from tennisd.models import FollowedPlayer, IngestionRun, LiveMatch, Notification, Player, User
from tennisd.routes import current_match_rows, player_live_matches
from tennisd.snapshot_policy import current_snapshot_condition, snapshot_counts, snapshot_visibility_reason
from scripts.sync_live_tennis import notify_followers


class SnapshotPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True,"SECRET_KEY":"snapshot-tests-only",
            "SQLALCHEMY_DATABASE_URI":f"sqlite:///{Path(self.temp.name)/'db'}"})
        self.ctx = self.app.app_context(); self.ctx.push()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.clock = patch("tennisd.snapshot_policy.utcnow",return_value=self.now); self.clock.start()
        self.api_clock = patch("tennisd.live_routes.utcnow",return_value=self.now); self.api_clock.start()

    def tearDown(self):
        self.api_clock.stop(); self.clock.stop(); db.session.remove(); db.engine.dispose(); self.ctx.pop(); self.temp.cleanup()

    def row(self, identifier, *, status="live", age=timedelta(), start=timedelta(), round_code="R32", end=None):
        row=LiveMatch(provider_id=identifier,status=status,tour="ATP",tournament="Snapshot Test",surface="Hard",
            round=round_code,draw="singles",is_doubles=False,player1_name="Carlos Alcaraz",player2_name="Jannik Sinner",
            score="6–4 2–1",synced_at=self.now-age,starts_at=self.now+start if start is not None else None,
            finished_at=self.now+end if end is not None else None)
        db.session.add(row)
        return row

    def test_sql_and_detail_policy_agree_at_all_window_boundaries(self):
        us=timedelta(microseconds=1)
        cases=[("age-edge",dict(age=timedelta(hours=30)),True),
            ("age-old",dict(age=timedelta(hours=30)+us),False),
            ("clock-edge",dict(age=-timedelta(minutes=5)),True),
            ("clock-bad",dict(age=-timedelta(minutes=5)-us),False),
            ("live-old-edge",dict(start=-timedelta(hours=72)),True),
            ("live-old",dict(start=-timedelta(hours=72)-us),False),
            ("live-future-edge",dict(start=timedelta(hours=6)),True),
            ("live-future",dict(start=timedelta(hours=6)+us),False),
            ("live-undated",dict(start=None),True),
            ("up-old-edge",dict(status="upcoming",start=-timedelta(hours=6)),True),
            ("up-old",dict(status="upcoming",start=-timedelta(hours=6)-us),False),
            ("up-edge",dict(status="upcoming",start=timedelta(days=7)),True),
            ("up-far",dict(status="upcoming",start=timedelta(days=7)+us),False),
            ("up-undated",dict(status="upcoming",start=None),False),
            ("finished-edge",dict(status="finished",end=-timedelta(days=7)),True),
            ("finished-old",dict(status="finished",end=-timedelta(days=7)-us),False),
            ("finished-future",dict(status="finished",end=timedelta(minutes=6)),False),
            ("finished-undated",dict(status="finished",start=None),False),
            ("verifying",dict(status="verifying"),False),
            ("cancelled",dict(status="cancelled"),False),
            ("hidden-round",dict(round_code="R128"),False)]
        for key,kwargs,_ in cases:self.row(key,**kwargs)
        db.session.commit()
        actual=set(db.session.scalars(select(LiveMatch.provider_id).where(current_snapshot_condition(self.now))).all())
        self.assertEqual(actual,{key for key,_,ok in cases if ok})
        for key,_,ok in cases:
            self.assertEqual(snapshot_visibility_reason(db.session.get(LiveMatch,key),self.now) is None,ok,key)

    def test_lists_api_and_profile_hide_old_rows_but_direct_report_survives(self):
        old=self.row("old-report",age=timedelta(days=2),start=-timedelta(days=20))
        current=self.row("current-live")
        current.winner_side=1
        self.row("old-date-new-check",start=-timedelta(days=20))
        self.row("upcoming",status="upcoming",start=timedelta(days=2))
        db.session.commit()
        before=(old.status,old.score,old.winner_side,old.synced_at)
        client=self.app.test_client()
        self.assertEqual([r.provider_id for r in current_match_rows("live")],["current-live"])
        self.assertEqual([r.provider_id for r in player_live_matches(db.session.get(Player,"atp-207989"))],["current-live","upcoming"])
        api=client.get('/api/live-matches').get_json()
        self.assertEqual([r['id'] for r in api['matches']],["current-live"])
        body=client.get('/matches?view=live').get_data(as_text=True)
        self.assertNotIn('old-report',body)
        page=client.get('/live-matches/old-report')
        self.assertEqual(page.status_code,200)
        text=page.get_data(as_text=True)
        self.assertIn('Outside the current feed',text)
        self.assertIn('Current status unconfirmed',text)
        self.assertIn('LAST REPORTED STATUS',text)
        self.assertNotIn('data-live-match=',text)
        current_body=client.get('/live-matches/current-live').get_data(as_text=True)
        self.assertNotIn('<small>WINNER</small>',current_body)
        self.assertIn('data-winner-side="0"',current_body)
        self.assertIsNone(api['matches'][0]['winner_side'])
        self.assertEqual((old.status,old.score,old.winner_side,old.synced_at),before)
        self.assertEqual(snapshot_counts(self.now)['live'],{'current':1,'outside_current_window':2})

    def test_old_rows_do_not_consume_profile_limit_before_current_rows(self):
        for i in range(20):self.row(f'old-{i}',age=timedelta(days=3))
        self.row('fresh-profile')
        db.session.commit()
        self.assertEqual([m.provider_id for m in player_live_matches(db.session.get(Player,'atp-207989'),limit=1)],['fresh-profile'])

    def test_notifications_skip_expired_schedules_future_checks_and_hidden_rounds(self):
        user=User(username='snapshotfan',email='snapshotfan@example.test',display_name='Fan',password_hash='test-only')
        db.session.add(user); db.session.flush()
        db.session.add(FollowedPlayer(user_id=user.id,player_id='atp-207989'))
        self.row('eligible-alert')
        self.row('old-schedule-alert',start=-timedelta(days=20))
        self.row('expired-alert',age=timedelta(days=2))
        self.row('future-check-alert',age=-timedelta(days=1))
        self.row('hidden-alert',round_code='R128')
        self.row('distant-alert',status='upcoming',start=timedelta(days=8))
        db.session.commit()
        self.assertEqual(notify_followers(self.now),1)
        self.assertEqual(notify_followers(self.now),0)
        notes=db.session.scalars(select(Notification)).all()
        self.assertEqual([n.url for n in notes],['/live-matches/eligible-alert'])

    def test_status_uses_latest_attempt_per_feed_even_after_many_history_runs(self):
        for feed in ('live','upcoming'):
            db.session.add(IngestionRun(provider='livetennisapi:'+feed,status='success',started_at=self.now,
                finished_at=self.now,records_written=2))
        for i in range(25):
            db.session.add(IngestionRun(provider='livetennisapi:history',status='success',started_at=self.now+timedelta(seconds=i),finished_at=self.now))
        db.session.commit()
        client=self.app.test_client()
        response=client.get('/api/sync-status');data=response.get_json()
        self.assertEqual(data['status'],'ok')
        self.assertEqual(set(data['feeds']),{'live','upcoming'})
        self.assertIn('snapshots',data)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        db.session.add(IngestionRun(provider='livetennisapi:live',status='failed',started_at=self.now,
            finished_at=self.now,error='DO_NOT_LEAK_PROVIDER_KEY'))
        db.session.commit()
        data=client.get('/api/sync-status').get_json()
        self.assertEqual(data['status'],'degraded')
        self.assertTrue(data['feeds']['live']['has_error'])
        self.assertNotIn('DO_NOT_LEAK_PROVIDER_KEY',json.dumps(data))
