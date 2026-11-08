"""Identity collisions and recovery previews must not rewrite a member's diary."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import requests
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

from tennisd import create_app, db
from tennisd.catalog_audit import catalog_report, readonly_catalog_session
from tennisd.catalog_recovery import recovery_plan
from tennisd.identities import PlayerIdentityIndex
from tennisd.live_tennis import ensure_player_profiles, live_player_id, normalize_history_match, normalize_match
from tennisd.models import FollowedPlayer, LiveMatch, Match, Notification, Player, PlayerExternalId, Review, User
from tennisd.routes import archived_match_id_for_live, current_match_players, player_live_matches
from scripts.sync_live_tennis import feed_due, mark_feed, notify_followers
from test_live_tennis import Session, fixture, history_fixture


class CatalogRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "recovery-tests-only",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.temp.name) / 'test.db'}",
        })
        self.context = self.app.app_context()
        self.context.push()
        self.key = patch.dict(os.environ, {"LIVETENNISAPI_KEY": "do-not-publish-this-test-key"})
        self.key.start()
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.key.stop()
        self.temp.cleanup()

    def cross_tour_match(self, identifier="lt-81234", provider="livetennisapi", source="livetennisapi:81234"):
        match = Match(
            id=identifier, provider=provider, provider_id=source, status="finished", tour="ATP",
            tournament="China Open", week_start=self.now.date(), round="F", surface="Hard", level="A",
            score="6-4 6-4", winner=db.session.get(Player, "atp-207989"), loser=db.session.get(Player, "wta-216347"),
        )
        db.session.add(match)
        db.session.commit()
        return match

    def test_daily_feed_is_due_even_if_todays_scheduler_runs_earlier(self):
        mark_feed("live", datetime(2026,10,31,8,22,tzinfo=timezone.utc))
        self.assertTrue(feed_due("live", datetime(2026,11,1,2,7,tzinfo=timezone.utc)))
        mark_feed("live", datetime(2026,11,1,2,8,tzinfo=timezone.utc))
        self.assertTrue(feed_due("live", datetime(2026,11,2,2,7,tzinfo=timezone.utc)))

    def test_daily_feed_does_not_repeat_within_the_same_utc_day(self):
        mark_feed("upcoming", datetime(2026,11,1,0,7,tzinfo=timezone.utc))
        self.assertFalse(feed_due("upcoming", datetime(2026,11,1,22,7,tzinfo=timezone.utc)))
        self.assertTrue(feed_due("live", datetime(2026,11,1,22,7,tzinfo=timezone.utc)))

    def test_same_source_id_cannot_represent_both_opponents(self):
        for factory, normalize in ((fixture, normalize_match), (history_fixture, normalize_history_match)):
            row = factory()
            row["players"]["p2"]["id"] = row["players"]["p1"]["id"]
            self.assertIsNone(normalize(row))

    def test_ambiguous_names_are_never_resolved_by_row_order(self):
        db.session.add_all([Player(id="test-a", tour="ATP", name="Alex Example"), Player(id="test-b", tour="ATP", name="Alex Example")])
        db.session.commit()
        self.assertIsNone(PlayerIdentityIndex([("ATP", "Alex Example", None)]).resolve("ATP", "Alex Example", None))
        ensure_player_profiles([("ATP", "Alex Example", "source-a")])
        db.session.commit()
        mapping = db.session.scalar(select(PlayerExternalId).where(PlayerExternalId.external_id == "source-a"))
        self.assertEqual(mapping.player_id, live_player_id("ATP", "source-a", "Alex Example"))
        self.assertNotIn(mapping.player_id, ("test-a", "test-b"))

    def test_one_provider_cannot_map_two_ids_to_a_name_only_candidate(self):
        existing = db.session.get(Player, "atp-207989")
        db.session.add(PlayerExternalId(player=existing, provider="livetennisapi", external_id="original"))
        db.session.commit()
        ensure_player_profiles([("ATP", existing.name, "another")])
        db.session.commit()
        mapping = db.session.scalar(select(PlayerExternalId).where(PlayerExternalId.external_id == "another"))
        self.assertNotEqual(mapping.player_id, existing.id)
        self.assertEqual(db.session.scalar(select(PlayerExternalId.player_id).where(PlayerExternalId.external_id == "original")), existing.id)

    def test_namesakes_in_one_batch_get_distinct_repeatable_identities(self):
        participants = [("ATP", "Carlos Alcaraz", "namesake-1"), ("ATP", "Carlos Alcaraz", "namesake-2")]
        self.assertEqual(ensure_player_profiles(participants), 2)
        db.session.commit()
        index = PlayerIdentityIndex(participants)
        first = index.resolve(*participants[0])
        second = index.resolve(*participants[1])
        self.assertNotEqual(first.id, second.id)
        self.assertNotEqual(first.id, "atp-207989")
        self.assertNotEqual(second.id, "atp-207989")
        self.assertEqual(ensure_player_profiles(participants), 0)

    def test_live_cards_and_profile_do_not_link_a_namesake(self):
        player = db.session.get(Player, "atp-207989")
        db.session.add(PlayerExternalId(player=player, provider="livetennisapi", external_id="known"))
        live = LiveMatch(provider_id="namesake-live", status="upcoming", tour="ATP", tournament="Namesake Open", surface="Hard",
            starts_at=self.now, synced_at=self.now, player1_name=player.name, player1_provider_id="different",
            player2_name="Jannik Sinner", player2_provider_id=None)
        db.session.add(live);db.session.commit()
        self.assertIsNone(current_match_players([live])[live.provider_id][0])
        self.assertEqual(player_live_matches(player), [])

    def test_namesake_cannot_trigger_a_follow_notification(self):
        old = db.session.get(Player, "atp-207989")
        db.session.add(PlayerExternalId(player=old, provider="livetennisapi", external_id="known"))
        ensure_player_profiles([("ATP", old.name, "different")]);db.session.commit()
        new = db.session.scalar(select(Player).join(PlayerExternalId).where(PlayerExternalId.external_id == "different"))
        for identifier, player in (("oldfan", old), ("newfan", new)):
            user = User(username=identifier, email=f"{identifier}@example.test", display_name=identifier, password_hash="unused-test-value")
            db.session.add(user);db.session.flush()
            db.session.add(FollowedPlayer(user_id=user.id, player_id=player.id))
        db.session.add(LiveMatch(provider_id="namesake-notify", status="upcoming", tour="ATP", tournament="Example", surface="Hard",
            starts_at=self.now, synced_at=self.now, player1_name=old.name, player1_provider_id="different", player2_name="Jannik Sinner"))
        db.session.commit()
        self.assertEqual(notify_followers(self.now), 1)
        note = db.session.scalar(select(Notification))
        self.assertEqual(db.session.get(User, note.user_id).username, "newfan")

    def test_archive_redirect_requires_unambiguous_profiles_and_same_event(self):
        archive = db.session.get(Match, "atp-2025-560-226")
        live = LiveMatch(provider_id="other-event", status="finished", tour="ATP", tournament="Unrelated Event", surface="Hard",
            starts_at=datetime.combine(archive.week_start, datetime.min.time(), tzinfo=timezone.utc),
            player1_name=archive.winner.name, player2_name=archive.loser.name)
        self.assertIsNone(archived_match_id_for_live(live))
        live.tournament = archive.tournament
        self.assertEqual(archived_match_id_for_live(live), archive.id)
        db.session.add(Player(id="namesake-redirect", tour="ATP", name=archive.winner.name));db.session.commit()
        self.assertIsNone(archived_match_id_for_live(live))

    def test_report_groups_sources_and_never_exports_private_content(self):
        match = self.cross_tour_match()
        self.cross_tour_match("ta-atp-111", "tennis_api", "tennis_api:atp:111")
        user = User(username="privateaccount", email="private@example.test", display_name="Private", password_hash="unused-test-value")
        db.session.add(Review(user=user, match=match, watched_on=self.now.date(), body="private diary text", is_public=False));db.session.commit()
        with readonly_catalog_session():
            report = catalog_report(1)
        self.assertEqual(report["identity_reason_counts"]["cross_tour"], 2)
        self.assertEqual({row["provider"] for row in report["invalid_by_source"]}, {"tennis_api", "livetennisapi"})
        self.assertEqual(len(report["invalid_match_samples"]), 1)
        self.assertNotIn("privateaccount", json.dumps(report))
        self.assertNotIn("private diary text", json.dumps(report))
        self.assertNotIn("private@example.test", json.dumps(report))

    def test_readonly_transaction_rejects_writes_and_resets_afterwards(self):
        with self.assertRaises(OperationalError):
            with readonly_catalog_session():
                db.session.execute(text("UPDATE player SET name='bad' WHERE id='atp-207989'"))
        self.assertEqual(db.session.get(Player, "atp-207989").name, "Carlos Alcaraz")
        db.session.rollback()
        db.session.execute(text("UPDATE player SET name=name WHERE id='atp-207989'"));db.session.rollback()

    def test_preview_retired_source_does_not_spend_live_provider_quota(self):
        self.cross_tour_match("ta-atp-111", "tennis_api", "tennis_api:atp:111")
        session = Session([])
        with readonly_catalog_session():
            plan = recovery_plan(session=session)
        self.assertEqual(plan["provider_requests"], 0)
        self.assertEqual(session.calls, [])
        self.assertEqual(plan["proposals"][0]["decision"], "needs_original_source")

    def test_preview_proposes_result_without_writing_players_matches_or_cursors(self):
        match = self.cross_tour_match()
        before = (match.winner_id, match.loser_id, match.score)
        player_count = db.session.scalar(select(func.count(Player.id)))
        db.session.commit()
        with readonly_catalog_session():
            plan = recovery_plan(session=Session([], {"81234": history_fixture()}))
        proposal = plan["proposals"][0]
        self.assertEqual(proposal["decision"], "ready_for_review")
        self.assertEqual(proposal["source_result"]["loser"]["name"], "Jannik Sinner")
        self.assertIn("sha256", proposal["evidence"])
        self.assertEqual((match.winner_id, match.loser_id, match.score), before)
        self.assertEqual(db.session.scalar(select(func.count(Player.id))), player_count)
        self.assertEqual(db.session.scalar(select(func.count(PlayerExternalId.id))), 0)

    def test_preview_surfaces_mapping_conflict_without_reassigning_it(self):
        self.cross_tour_match()
        wrong = db.session.get(Player, "wta-216347")
        db.session.add(PlayerExternalId(player=wrong, provider="livetennisapi", external_id="102"));db.session.commit()
        with readonly_catalog_session():
            plan = recovery_plan(session=Session([], {"81234": history_fixture()}))
        self.assertEqual(plan["proposals"][0]["decision"], "blocked_identity_mapping")
        self.assertEqual(plan["proposals"][0]["source_result"]["loser"]["blocked"], "conflicting_external_id")
        self.assertEqual(db.session.scalar(select(PlayerExternalId.player_id)), wrong.id)

    def test_missing_provider_result_is_unresolved_without_changes(self):
        self.cross_tour_match()
        with readonly_catalog_session():
            plan = recovery_plan(session=Session([]))
        self.assertEqual(plan["proposals"][0]["decision"], "source_not_found")
        self.assertEqual(db.session.get(Match, "lt-81234").status, "finished")

    def test_preview_stops_on_quota_error_without_leaking_credentials(self):
        self.cross_tour_match()
        self.cross_tour_match("lt-81235", source="livetennisapi:81235")
        class QuotaSession:
            calls = 0
            def get(self, *args, **kwargs):
                self.calls += 1
                response = requests.Response();response.status_code = 429
                response.url = "https://example.test/?key=do-not-publish-this-test-key"
                return response
        session = QuotaSession()
        with readonly_catalog_session():
            plan = recovery_plan(limit=2, session=session)
        self.assertEqual(session.calls, 1)
        self.assertEqual(plan["proposals"][1]["decision"], "provider_requests_stopped")
        self.assertNotIn("do-not-publish", json.dumps(plan))
