"""Large diaries must stay bounded without leaking private rows or totals."""

import tempfile
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from flask import template_rendered
from sqlalchemy import event, select

from tennisd import create_app, db
from tennisd.models import Friendship, Match, Player, Review, User, WatchlistItem
from tennisd.stats import diary_statistics


class ProfilePaginationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "pagination-tests-only",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.temp.name) / 'test.db'}"})
        self.client = self.app.test_client()
        self.public_ids, self.all_ids, self.favorite_ids = [], [], []
        self.ratings = []
        with self.app.app_context():
            owner = User(username="paginator", email="paginator@example.test", display_name="Paginator", password_hash="unused-test-value")
            visitor = User(username="visitor", email="visitor@example.test", display_name="Visitor", password_hash="unused-test-value")
            hidden_player = Player(id="hidden-player", tour="ATP", name="PRIVATE ATHLETE")
            db.session.add_all((owner, visitor, hidden_player));db.session.flush()
            self.owner_id, self.visitor_id = owner.id, visitor.id
            first, second = db.session.get(Player, "atp-207989"), db.session.get(Player, "atp-206173")
            for i in range(61):
                public = i % 3 != 0
                favorite = i % 2 == 0
                rating = (i % 10) + 1 if i % 5 else None
                match = Match(id=f"page-test-{i:03}", tour="ATP", tournament=f"{'VISIBLE' if public else 'PRIVATE'}_EVENT_{i:03}",
                    level="A", surface=("Hard", "Clay", "Grass")[i % 3], week_start=date(2026,1,1), round="F",
                    winner=hidden_player if i == 0 else first, loser=second, score="6-1 6-2")
                review = Review(user=owner, match=match, watched_on=date(2026,10,31), is_public=public, is_favorite=favorite,
                    rating_half=rating, body=f"{'VISIBLE' if public else 'PRIVATE'}_NOTE_{i:03}")
                db.session.add_all((match, review));db.session.flush()
                self.all_ids.append(review.id)
                if public:self.public_ids.append(review.id)
                if favorite:self.favorite_ids.append(review.id)
                self.ratings.append(rating)
                db.session.add(WatchlistItem(user_id=owner.id, match_id=match.id, added_at=datetime(2026,10,31,tzinfo=timezone.utc)))
            db.session.add(Review(user=visitor, match_id="page-test-001", watched_on=date(2026,10,31), rating_half=10, is_favorite=True, body="ANOTHER_ACCOUNT_NOTE"))
            for i in range(20):
                friend = User(username=f"privatefriend{i:02}", email=f"friend{i}@example.test", display_name=f"Private Friend {i}", password_hash="unused-test-value")
                db.session.add(friend);db.session.flush()
                db.session.add(Friendship(requester_id=owner.id if i % 2 else friend.id, addressee_id=friend.id if i % 2 else owner.id,
                    status="accepted" if i < 19 else "pending", created_at=datetime(2026,10,31,tzinfo=timezone.utc)))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove();db.engine.dispose()
        self.temp.cleanup()

    def sign_in(self, user_id=None):
        with self.client.session_transaction() as session:
            session["_user_id"] = f"{user_id or self.owner_id}:0"
            session["_fresh"] = True

    @contextmanager
    def contexts(self):
        contexts = []
        def record(sender, template, context, **extra):contexts.append(context)
        template_rendered.connect(record, self.app)
        try:yield contexts
        finally:template_rendered.disconnect(record, self.app)

    def profile(self, query=""):
        with self.contexts() as contexts:
            response = self.client.get(f"/u/paginator{query}")
        self.assertEqual(response.status_code, 200)
        return response, contexts[-1]

    def test_owner_diary_pages_have_no_omissions_or_duplicates_for_tied_dates(self):
        self.sign_in()
        ids = []
        for page in range(1,5):
            response, context = self.profile(f"?tab=diary&page={page}")
            self.assertEqual(context["pagination"].total, 61)
            self.assertLessEqual(len(context["reviews"]), 18)
            ids.extend(item.id for item in context["reviews"])
            self.assertIn(f"Page {page} of 4".encode(), response.data)
        self.assertEqual(ids, list(reversed(self.all_ids)))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(response.cache_control.private)
        self.assertTrue(response.cache_control.no_store)

    def test_visitors_cannot_see_private_rows_totals_or_statistics_on_any_page(self):
        for visitor_id in (None, self.visitor_id):
            if visitor_id:self.sign_in(visitor_id)
            ids = []
            for page in range(1,4):
                response, context = self.profile(f"?tab=diary&page={page}")
                self.assertEqual(context["pagination"].total, len(self.public_ids))
                self.assertNotIn(b"PRIVATE_EVENT", response.data)
                self.assertNotIn(b"PRIVATE_NOTE", response.data)
                self.assertNotIn(b"ANOTHER_ACCOUNT_NOTE", response.data)
                ids.extend(item.id for item in context["reviews"])
            self.assertEqual(ids, list(reversed(self.public_ids)))
            _, context = self.profile()
            self.assertEqual(context["stats"]["logs"], len(self.public_ids))
            self.assertNotIn("hidden-player", [item["id"] for item in context["stats"]["players"]])

    def test_dashboard_statistics_cover_the_full_diary_and_lists_stay_small(self):
        self.sign_in()
        _, context = self.profile("?page=999999")
        stats = context["stats"]
        self.assertEqual(stats["logs"], 61)
        self.assertEqual(stats["favorites"], len(self.favorite_ids))
        rated = [value for value in self.ratings if value is not None]
        self.assertEqual(stats["average_ten"], round(sum(rated) / len(rated),1))
        self.assertEqual(stats["rated_count"], len(rated))
        self.assertEqual(sum(count for _,count in stats["surfaces"]),61)
        self.assertEqual(sum(count for _,count in stats["rating_scale"]),len(rated))
        self.assertEqual(len(context["reviews"]),5)
        self.assertEqual(len(context["favorites"]),4)
        self.assertEqual(context["watchlist"],[])
        self.assertEqual(context["friends"],[])

    def test_favorites_have_their_own_pagination_and_privacy_filter(self):
        self.sign_in()
        ids=[]
        for page in (1,2):
            _, context = self.profile(f"?tab=likes&page={page}")
            self.assertEqual(context["pagination"].total,len(self.favorite_ids))
            ids.extend(item.id for item in context["favorites"])
        self.assertEqual(ids,list(reversed(self.favorite_ids)))
        self.sign_in(self.visitor_id)
        response, context = self.profile("?tab=likes&page=2")
        expected = set(self.public_ids) & set(self.favorite_ids)
        self.assertEqual(context["pagination"].total,len(expected))
        self.assertNotIn(b"PRIVATE_EVENT",response.data)
        self.assertTrue(all(item.id in expected for item in context["favorites"]))

    def test_private_collections_are_bounded_and_unavailable_to_other_members(self):
        self.sign_in()
        for tab, total in (("watchlist",61),("friends",19)):
            _, context=self.profile(f"?tab={tab}&page=2")
            self.assertEqual(context["pagination"].total,total)
            self.assertLessEqual(len(context[tab]),18)
            self.assertEqual(context["reviews"],[])
            self.assertIsNone(context["stats"])
        self.sign_in(self.visitor_id)
        for tab in ("watchlist","friends"):
            response,context=self.profile(f"?tab={tab}&page=2")
            self.assertEqual(context["active_tab"],"profile")
            self.assertNotIn(b"Private Friend",response.data)
            self.assertEqual(context["watchlist"],[])
            self.assertEqual(context["friends"],[])
            self.assertIsNone(context["pagination"])

    def test_invalid_or_huge_pages_are_clamped_before_sql_offsets(self):
        self.sign_in()
        for value,expected in (("invalid",1),("0",1),("-10",1),("9"*100,4)):
            _,context=self.profile(f"?tab=diary&page={value}&per_page=1000000")
            self.assertEqual(context["pagination"].page,expected)
            self.assertLessEqual(len(context["reviews"]),18)
        response,_=self.profile("?tab=diary&page=2")
        self.assertIn(b'tab=diary&amp;page=1',response.data)
        self.assertIn(b'tab=diary&amp;page=3',response.data)

    def test_empty_diary_has_zero_statistics_and_no_fake_navigation(self):
        self.sign_in(self.visitor_id)
        with self.app.app_context():
            owner=User(username="empty",email="empty@example.test",display_name="Empty",password_hash="unused-test-value")
            db.session.add(owner);db.session.commit()
        response=self.client.get('/u/empty?tab=diary&page=999999')
        self.assertEqual(response.status_code,200)
        self.assertNotIn(b'aria-label="Diary pages"',response.data)
        with self.app.app_context():
            empty_id=db.session.scalar(select(User.id).where(User.username=="empty"))
            stats=diary_statistics(empty_id,public_only=True)
        self.assertEqual(stats["logs"],0)
        self.assertIsNone(stats["average_ten"])
        self.assertEqual(stats["players"],[])

    def test_profile_request_does_not_hydrate_the_whole_diary(self):
        self.sign_in()
        for tab,bound in (("profile",9),("diary",18),("likes",18),("watchlist",0),("friends",0)):
            loaded=[]
            def record(target,context):loaded.append(target.id)
            event.listen(Review,"load",record)
            try:self.profile(f"?tab={tab}")
            finally:event.remove(Review,"load",record)
            self.assertLessEqual(len(loaded),bound,tab)

    def test_export_is_complete_even_when_the_diary_is_paginated(self):
        self.sign_in()
        response=self.client.get('/settings/export')
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.get_json()["diary"]),61)

    def test_same_name_players_remain_separate_in_summary(self):
        with self.app.app_context():
            player=db.session.get(Player,"hidden-player");player.name="Carlos Alcaraz";db.session.commit()
            stats=diary_statistics(self.owner_id)
        same_named=[item for item in stats["players"] if item["name"]=="Carlos Alcaraz"]
        self.assertEqual({item["id"] for item in same_named},{"hidden-player","atp-207989"})
        self.assertEqual(sorted(item["count"] for item in same_named),[1,60])
