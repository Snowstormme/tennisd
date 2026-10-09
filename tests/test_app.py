import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from tennisd import create_app, db
from tennisd.models import Comment, FeedbackSubmission, FollowedPlayer, LiveMatch, Match, Player, Poll, PollOption, PollVote, ProfileImage, RankingSnapshot, Report, Review, TournamentSubscription, User, WatchlistItem
from tennisd.news_feed import NEWS_SOURCES, curate_news_items, fetch_news_items
from tennisd.routes import normalized_person_name, wikimedia_player_photo
from tennisd.tournament_catalog import tournament_slug
from sqlalchemy import select


class TennisdFlows(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "test.db"
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-only-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database}",
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            match = db.session.scalar(select(Match).limit(1))
            self.match_id = match.id
            self.tournament_name = match.tournament
            self.tournament_tour = match.tour
            self.tournament_year = match.week_start.year

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temporary.cleanup()

    def token(self):
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def register(self, name):
        self.client.get("/register")
        return self.client.post("/register", data={
            "csrf_token": self.token(), "username": name,
            "email": f"{name}@example.com", "password": "long-test-password",
        }, follow_redirects=True)

    def test_core_pages_and_search(self):
        for path in ("/", "/matches", "/players", "/players?view=rankings", "/tournaments", "/search", "/news", "/about", "/privacy", "/feedback", f"/matches/{self.match_id}"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        home = self.client.get("/")
        self.assertIn(b"<title>Tennisd \xc2\xb7 Tennis Match Diary</title>", home.data)
        self.assertIn(b'application/ld+json', home.data)
        self.assertIn(b'"name": "Tennisd"', home.data)
        self.assertIn(b"Tennisd is your tennis match diary", home.data)
        self.assertNotIn(b"hero-counts", home.data)
        self.assertNotIn(b"Start your diary", home.data)
        self.assertNotIn(b"Make every watch count", home.data)
        self.assertIn(b"data-featured-carousel", home.data)
        self.assertIn(b"home-match-card", home.data)
        self.assertIn(b"court-badge", home.data)
        self.assertIn(b"/photo", home.data)
        self.assertNotIn(b"nav-discover", home.data)
        for item in (b"nav-matches", b"nav-players", b"nav-tournaments", b"nav-notifications", b"nav-news", b"nav-search"):
            self.assertIn(item, home.data)
        self.assertNotIn(b"nav-rankings", home.data)
        self.assertIn(b"mobile-notifications", home.data)
        self.assertIn(b'href="/feedback"', home.data)
        self.assertIn(b"<em></em><strong></strong><i></i><b></b>", home.data)
        register = self.client.get("/register")
        self.assertIn(b'data-password-toggle', register.data)
        self.assertIn(b'aria-controls="auth-password"', register.data)
        with self.client.get("/static/app.js") as script:
            self.assertIn(b"data-password-toggle", script.data)
            self.assertNotIn(b"is-animating", script.data)
        self.assertIn(b"Jannik Sinner", self.client.get("/matches?q=Jannik+Sinner").data)
        search = self.client.get("/search?q=Jannik+Sinner")
        self.assertIn(b"search-people-grid", search.data)
        self.assertIn(b"search-match-grid", search.data)
        self.assertIn(b"home-match-card", search.data)
        self.assertIn(b"match-portrait", search.data)
        self.assertEqual(self.client.get("/matches?tour=WTA").status_code, 200)
        matches = self.client.get("/matches")
        self.assertIn(b"archive-match-card", matches.data)
        self.assertIn(b"court-badge", matches.data)
        self.assertIn(b"match-portrait-left", matches.data)
        players = self.client.get("/players")
        self.assertIn(b"player-photo-card", players.data)

        self.assertIn(b"/photo", players.data)
        self.assertIn(b">All</option>", players.data)
        self.assertNotIn(b"ATP + WTA", players.data)
        self.assertIn(b"MOST WATCHED IN THE CATALOG", players.data)
        rankings = self.client.get("/players?view=rankings")
        self.assertIn(b"ranked players", rankings.data)
        self.assertEqual(self.client.get("/rankings").status_code, 301)
        tournaments = self.client.get("/tournaments")
        self.assertIn(b"Go straight to a tournament", tournaments.data)
        self.assertIn(b"tournament-browser", tournaments.data)
        self.assertIn(b"Main tournaments", tournaments.data)
        self.assertIn(b"tournament-card-trophy", tournaments.data)
        self.assertNotIn(b"trophy-mark", tournaments.data)
        self.assertIn(b"Tournament value", tournaments.data)

        tournament_path = (
            f"/tournaments/{self.tournament_tour.lower()}/"
            f"{tournament_slug(self.tournament_name)}"
        )
        tournament = self.client.get(tournament_path)
        self.assertEqual(tournament.status_code, 200)
        self.assertIn(b"tournament-hero", tournament.data)
        self.assertIn(b"TITLE LEADERS", tournament.data)
        self.assertIn(b"Image:", tournament.data)
        self.assertIn(b'data-tournament-theme="court"', tournament.data)
        self.assertIn(b"+ Follow tournament", tournament.data)
        edition = self.client.get(f"{tournament_path}/{self.tournament_year}")
        self.assertEqual(edition.status_code, 200)
        self.assertIn(b"TOURNAMENT EDITION", edition.data)
        jump = self.client.get(
            "/tournaments", query_string={
                "event": f"{self.tournament_tour}|{tournament_slug(self.tournament_name)}"
            },
        )
        self.assertEqual(jump.headers["Location"], tournament_path)

        news = self.client.get("/news?source=wta")
        self.assertIn(b"news-feed-section", news.data)
        self.assertIn(b'aria-current="page">WTA</a>', news.data)
        self.assertIn(b"Information sources", news.data)
        self.assertNotIn(b"news-story-arrow", news.data)
        self.assertNotIn("↗".encode(), news.data)
        self.assertIn(b"data-news-live", news.data)
        self.assertNotIn(b"Updates every minute", news.data)
        self.assertNotIn(b"news-live-status", news.data)
        self.assertLess(news.data.index(b"news-feed-section"), news.data.index(b"news-sources-section"))
        with self.client.get("/static/app.js") as script:
            self.assertIn(b"/api/news-status", script.data)
        news_status = self.client.get("/api/news-status?source=wta")
        self.assertEqual(news_status.json["total"], 0)
        self.assertEqual(news_status.headers["Cache-Control"], "no-store")
        about = self.client.get("/about")
        self.assertIn(b"Tennis API on RapidAPI", about.data)
        self.assertNotIn(b"Jeff", about.data)
        self.assertNotIn(b"Sackmann", about.data)

    def test_guest_feedback_and_poll_vote_are_stored_without_personal_details(self):
        with self.app.app_context():
            poll = Poll(question="What should improve next?")
            poll.options = [PollOption(label="Live scores", position=1), PollOption(label="Profiles", position=2)]
            db.session.add(poll)
            db.session.commit()
            poll_id = poll.id
            option_id = poll.options[0].id

        page = self.client.get("/feedback?from=/matches")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"What should improve next?", page.data)
        response = self.client.post("/feedback", data={
            "csrf_token": self.token(), "category": "idea", "rating": "4",
            "message": "Please add clearer live score alerts.", "page_path": "/matches",
        }, follow_redirects=True)
        self.assertIn(b"your feedback is now", response.data)
        vote = self.client.post(f"/feedback/polls/{poll_id}/vote", data={
            "csrf_token": self.token(), "option": option_id,
        }, follow_redirects=True)
        self.assertIn(b"Vote saved", vote.data)
        with self.app.app_context():
            item = db.session.scalar(select(FeedbackSubmission))
            self.assertIsNone(item.user_id)
            self.assertEqual(item.rating, 4)
            self.assertEqual(item.page_path, "/matches")
            stored_vote = db.session.scalar(select(PollVote))
            self.assertIsNone(stored_vote.user_id)
            self.assertEqual(len(stored_vote.visitor_key), 64)

    def test_match_sections_keep_archive_default_and_show_live_player_photos(self):
        with self.app.app_context():
            players = db.session.scalars(select(Player).order_by(Player.id).limit(2)).all()
            live = LiveMatch(
                provider_id="test-live-photo-card", status="live", tour="ATP",
                tournament="Test Open", surface="Hard", round="SF", draw="singles",
                player1_name=players[0].name, player2_name=players[1].name,
                score="6–4 2–1", starts_at=datetime.now(timezone.utc),
            )
            db.session.add(live)
            db.session.commit()
            player_ids = [player.id.encode() for player in players]

        archive = self.client.get("/matches")
        self.assertIn(b'aria-current="page">All matches</a>', archive.data)
        self.assertIn(b"listing-layout", archive.data)
        self.assertNotIn(b"test-live-photo-card", archive.data)

        live_page = self.client.get("/matches?view=live")
        self.assertIn(b'aria-current="page">Live</a>', live_page.data)
        self.assertIn(b"test-live-photo-card", live_page.data)
        self.assertIn(b"archive-match-card live-feed-match-card", live_page.data)
        self.assertIn(b"match-portrait-left", live_page.data)
        self.assertIn(b"match-portrait-right", live_page.data)
        for player_id in player_ids:
            self.assertIn(b"/players/" + player_id + b"/photo", live_page.data)
        self.assertNotIn(b"listing-layout", live_page.data)

    def test_live_player_photo_discovers_portrait_then_falls_back(self):
        with self.app.app_context():
            db.session.add(LiveMatch(
                provider_id="test-unmatched-photo", status="upcoming", tour="WTA",
                tournament="Test Open", surface="Clay", round="R32", draw="singles",
                player1_name="Unlisted Player", player2_name="Another Unlisted Player",
                starts_at=datetime.now(timezone.utc) + timedelta(hours=2),
            ))
            db.session.commit()

        page = self.client.get("/matches?view=upcoming")
        self.assertIn(b"/live-matches/test-unmatched-photo/players/1/photo", page.data)
        self.assertIn(b"/live-matches/test-unmatched-photo/players/2/photo", page.data)

        with patch("tennisd.routes.wikipedia_player_photo", return_value="https://upload.wikimedia.org/player.jpg"):
            photo = self.client.get("/live-matches/test-unmatched-photo/players/1/photo")
        self.assertEqual(photo.status_code, 302)
        self.assertEqual(photo.headers["Location"], "https://upload.wikimedia.org/player.jpg")

        with patch("tennisd.routes.wikipedia_player_photo", return_value=None), patch("tennisd.routes.wikidata_search_player_photo", return_value=None):
            fallback = self.client.get("/live-matches/test-unmatched-photo/players/2/photo")
        self.assertEqual(fallback.status_code, 200)
        self.assertEqual(fallback.mimetype, "image/svg+xml")
        self.assertIn(b"AU", fallback.data)

    def test_home_shows_recent_non_final_archive_matches(self):
        with self.app.app_context():
            db.session.query(Review).delete()
            db.session.query(Match).delete()
            first = Player(id="home-first", tour="ATP", name="Home Winner")
            second = Player(id="home-second", tour="ATP", name="Home Runner")
            db.session.add_all((first, second))
            db.session.flush()
            db.session.add(Match(
                id="home-r16-match",
                provider="livetennisapi",
                provider_id="livetennisapi:home-r16",
                status="finished",
                tour="ATP",
                tournament="Paris Masters",
                level="M",
                surface="Hard",
                week_start=datetime(2026, 10, 2, tzinfo=timezone.utc).date(),
                scheduled_at=datetime(2026, 10, 2, 12, tzinfo=timezone.utc),
                completed_at=datetime(2026, 10, 2, 14, tzinfo=timezone.utc),
                round="R16",
                winner=first,
                loser=second,
                score="6-4 6-4",
                best_of=3,
            ))
            db.session.commit()

        home = self.client.get("/")
        self.assertIn(b"data-featured-carousel", home.data)
        self.assertIn(b"Paris Masters", home.data)
        self.assertIn(b"Home Winner", home.data)

    def test_rankings_render_loaded_rows(self):
        with self.app.app_context():
            player = db.session.scalar(select(Player).limit(1))
            db.session.add(RankingSnapshot(
                player_id=player.id, ranked_on=datetime(2026, 9, 28).date(),
                ranking_type="singles", rank=7, source="test",
            ))
            db.session.commit()
            player_name = player.name.encode()
        response = self.client.get("/players?view=rankings")
        self.assertEqual(response.status_code, 200)
        self.assertIn(player_name, response.data)
        self.assertIn(b'player-ranking-badge">#7', response.data)
        searched = self.client.get(f"/players?view=rankings&q={player_name.decode()}")
        self.assertIn(player_name, searched.data)

    def test_search_engine_discovery_files(self):
        verification = self.client.get("/google872d566cb03fdad0.html")
        self.assertEqual(verification.status_code, 200)
        self.assertIn(b"google-site-verification", verification.data)
        current_verification = self.client.get("/google4394fddbd7a3b94e.html")
        self.assertEqual(current_verification.status_code, 200)
        self.assertIn(b"google4394fddbd7a3b94e.html", current_verification.data)

        robots = self.client.get("/robots.txt")
        self.assertEqual(robots.status_code, 200)
        self.assertIn(b"Sitemap: http://127.0.0.1:5000/sitemap.xml", robots.data)
        self.assertIn(b"Disallow: /settings", robots.data)

        sitemap = self.client.get("/sitemap.xml")
        self.assertEqual(sitemap.status_code, 200)
        self.assertIn(b"sitemap-core.xml", sitemap.data)
        self.assertIn(b"sitemap-players.xml", sitemap.data)
        self.assertIn(b"sitemap-tournaments.xml", sitemap.data)
        self.assertIn(b"sitemap-tournament-editions.xml", sitemap.data)
        self.assertIn(b"sitemap-matches-1.xml", sitemap.data)

        self.assertIn(b"/matches/", self.client.get("/sitemap-matches-1.xml").data)
        self.assertIn(b"/players/", self.client.get("/sitemap-players.xml").data)
        self.assertIn(b"/tournaments/", self.client.get("/sitemap-tournaments.xml").data)
        self.assertIn(b"/tournaments/", self.client.get("/sitemap-tournament-editions.xml").data)

        home = self.client.get("/")
        self.assertIn(b'rel="canonical"', home.data)
        self.assertIn(b'name="robots" content="index, follow', home.data)
        self.assertIn(b'property="og:site_name" content="Tennisd"', home.data)
        self.assertIn(b'property="og:title" content="Tennisd', home.data)
        self.assertIn(b'name="twitter:description"', home.data)
        self.assertIn(b'name="robots" content="noindex, follow"', self.client.get("/login").data)
        with self.app.app_context():
            player_id = db.session.scalar(select(Player.id).limit(1))
        profile = self.client.get(f"/players/{player_id}")
        self.assertIn(b"player-hero-photo", profile.data)
        self.assertIn(f"/players/{player_id}/photo".encode(), profile.data)
        self.assertIn(b'"@type": "Person"', profile.data)

        match = self.client.get(f"/matches/{self.match_id}")
        self.assertIn(b'"@type": "SportsEvent"', match.data)
        self.assertIn(b"Final score", match.data)

    def test_tournament_follow_is_saved_and_shown_in_profile(self):
        self.register("alice")
        tournament_path = (
            f"/tournaments/{self.tournament_tour.lower()}/"
            f"{tournament_slug(self.tournament_name)}"
        )
        response = self.client.post(f"{tournament_path}/subscribe", data={
            "csrf_token": self.token(),
        }, follow_redirects=True)
        self.assertIn(b"Following tournament", response.data)
        with self.app.app_context():
            self.assertIsNotNone(db.session.scalar(select(TournamentSubscription.id)))
        profile = self.client.get("/u/alice")
        self.assertIn(b"Tournaments you follow", profile.data)
        self.assertIn(self.tournament_name.encode(), profile.data)
        exported = self.client.get("/settings/export").get_json()
        self.assertEqual(exported["followed_tournaments"][0]["tournament"], self.tournament_name)

        response = self.client.post(f"{tournament_path}/subscribe", data={
            "csrf_token": self.token(),
        }, follow_redirects=True)
        self.assertIn(b"+ Follow tournament", response.data)
        with self.app.app_context():
            self.assertIsNone(db.session.scalar(select(TournamentSubscription.id)))

    def test_wikidata_portrait_and_accented_player_name(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "entities": {"Q123": {"claims": {"P18": [{
                "mainsnak": {"datavalue": {"value": "Player portrait.jpg"}}
            }]}}}
        }
        wikimedia_player_photo.cache_clear()
        with patch("tennisd.routes.requests.get", return_value=response):
            self.assertEqual(
                wikimedia_player_photo("Q123"),
                "https://commons.wikimedia.org/wiki/Special:Redirect/file/Player%20portrait.jpg?width=420",
            )
        self.assertEqual(normalized_person_name("Iga Świątek"), "iga swiatek")

    def test_news_story_opens_in_tennisd_reader(self):
        story_id = "A" * 40
        story = {
            "id": story_id, "title": "A final to remember", "source": "ATP Tour",
            "source_key": "atp", "url": f"https://news.google.com/rss/articles/{story_id}",
            "published_at": datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
        }
        article = {
            "original_url": "https://www.atptour.com/en/news/example",
            "description": "A concise publisher description of the final.",
            "paragraphs": ["A short publisher preview is available inside the Tennisd reader."],
            "image": "",
        }
        with patch("tennisd.routes.fetch_news_items", return_value=[story]), patch(
            "tennisd.routes.fetch_news_article", return_value=article,
        ):
            response = self.client.get(f"/news/atp/{story_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"news-reader", response.data)
        self.assertIn(b"A final to remember", response.data)
        self.assertIn(b"A short publisher preview", response.data)
        self.assertIn(b"Open on ATP Tour", response.data)

    def test_news_image_proxy_only_serves_an_allowed_publisher_image(self):
        story_id = "B" * 40
        story = {
            "id": story_id, "title": "A photographed final", "source": "ATP Tour",
            "source_key": "atp", "url": f"https://news.google.com/rss/articles/{story_id}",
            "published_at": datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
        }
        upstream = Mock()
        upstream.raise_for_status.return_value = None
        upstream.headers = {"Content-Type": "image/jpeg"}
        upstream.content = b"safe-image-bytes"
        with patch("tennisd.routes.fetch_news_items", return_value=[story]), patch(
            "tennisd.routes.fetch_news_article",
            return_value={"image": "https://www.atptour.com/-/media/example.jpg"},
        ), patch("tennisd.routes.requests.get", return_value=upstream):
            response = self.client.get(f"/news/atp/{story_id}/image")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/jpeg")
        self.assertIn("max-age=86400", response.headers["Cache-Control"])

    def test_news_feed_does_not_apply_the_old_24_story_limit(self):
        published = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)

        def source_items(source_key, _cache_window):
            return [{"id": f"{source_key}-{index}", "published_at": published} for index in range(40)]

        with patch("tennisd.news_feed._source_items", side_effect=source_items):
            stories = fetch_news_items("all", now=published)
        self.assertEqual(len(stories), 40 * len(NEWS_SOURCES))

    def test_news_curation_keeps_recent_and_only_important_older_stories(self):
        now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
        stories = [
            {"id": "today", "title": "A routine opening-round win", "published_at": now - timedelta(hours=3)},
            {"id": "today-copy", "title": "A routine opening-round win", "published_at": now - timedelta(hours=4)},
            {"id": "yesterday", "title": "Players arrive for the tournament", "published_at": now - timedelta(hours=30)},
            {"id": "old-routine", "title": "Practice gallery from the tour", "published_at": now - timedelta(days=3)},
            {"id": "old-major", "title": "World No. 1 withdraws with injury", "published_at": now - timedelta(days=4)},
            {"id": "older-routine", "title": "Another practice gallery", "published_at": now - timedelta(days=9)},
            {"id": "older-major", "title": "Former champion retires", "published_at": now - timedelta(days=9)},
            {"id": "too-old", "title": "Grand Slam champion retires", "published_at": now - timedelta(days=20)},
        ]
        selected = curate_news_items(stories, now=now)
        self.assertEqual(
            [story["id"] for story in selected],
            ["today", "yesterday", "old-routine", "old-major", "older-major"],
        )
        filled = curate_news_items(stories, now=now, minimum=6)
        self.assertEqual(len(filled), 6)
        self.assertIn("older-routine", {story["id"] for story in filled})

    def test_friend_requests_and_notifications(self):
        self.register("alice")
        self.client.post("/logout", data={"csrf_token": self.token()})
        self.register("bob")
        self.client.post("/u/alice/friend", data={"csrf_token": self.token()})
        self.client.post("/logout", data={"csrf_token": self.token()})
        self.client.get("/login")
        self.client.post("/login", data={
            "csrf_token": self.token(), "identity": "alice", "password": "long-test-password",
        })
        notifications = self.client.get("/notifications")
        self.assertIn(b"bob", notifications.data)
        with self.app.app_context():
            from tennisd.models import Friendship
            request_id = db.session.scalar(select(Friendship.id))
        self.client.post(f"/friend-requests/{request_id}/accept", data={"csrf_token": self.token()})
        self.assertIn(b"bob", self.client.get("/u/alice?tab=friends").data)

    def test_diary_comments_and_private_entries(self):
        self.assertEqual(self.register("alice").status_code, 200)
        response = self.client.post(f"/matches/{self.match_id}/log", data={
            "csrf_token": self.token(), "watched_on": "2025-01-30",
            "rating": "9", "body": "A final worth revisiting.", "public": "on",
            "favorite": "on",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"A final worth revisiting.", response.data)
        self.assertIn(b"9 / 10", response.data)
        self.assertIn(b"9.0<small>/ 10", response.data)
        profile = self.client.get("/u/alice")
        self.assertIn(b"9.0", profile.data)
        self.assertIn(b"profile-dashboard", profile.data)
        self.assertIn(b"favorite-four", profile.data)
        self.assertIn(b"profile-diary-list", profile.data)
        self.assertIn(b"rating-spectrum", profile.data)
        self.assertIn(b"9 out of 10: 1 match", profile.data)
        with self.app.app_context():
            review_id = db.session.scalar(select(Review.id))

        self.client.post("/logout", data={"csrf_token": self.token()})
        self.register("bob")
        response = self.client.post(f"/reviews/{review_id}/comments", data={
            "csrf_token": self.token(), "body": "That final set was special.",
        }, follow_redirects=True)
        self.assertIn(b"That final set was special.", response.data)

        self.client.post("/logout", data={"csrf_token": self.token()})
        self.client.get("/login")
        self.client.post("/login", data={
            "csrf_token": self.token(), "identity": "alice", "password": "long-test-password",
        })
        self.client.get(f"/matches/{self.match_id}")
        self.client.post(f"/matches/{self.match_id}/log", data={
            "csrf_token": self.token(), "watched_on": "2025-01-30",
            "rating": "9", "body": "A final worth revisiting.",
        })
        self.client.post("/logout", data={"csrf_token": self.token()})
        self.assertNotIn(b"A final worth revisiting.", self.client.get("/u/alice").data)
        self.assertNotIn(b"A final worth revisiting.", self.client.get(f"/matches/{self.match_id}").data)

    def test_csrf_and_review_ownership(self):
        self.register("alice")
        self.assertEqual(self.client.post(f"/matches/{self.match_id}/log", data={}).status_code, 400)
        self.client.post(f"/matches/{self.match_id}/log", data={
            "csrf_token": self.token(), "watched_on": "2025-01-30", "public": "on",
        })
        with self.app.app_context():
            review_id = db.session.scalar(select(Review.id))
        self.client.post("/logout", data={"csrf_token": self.token()})
        self.register("bob")
        self.assertEqual(
            self.client.post(f"/reviews/{review_id}/delete", data={"csrf_token": self.token()}).status_code,
            403,
        )

    def test_login_redirect_stays_on_site(self):
        self.register("alice")
        self.client.post("/logout", data={"csrf_token": self.token()})
        self.client.get("/login?next=/%5Cexample.com")
        response = self.client.post("/login?next=/%5Cexample.com", data={
            "csrf_token": self.token(), "identity": "alice", "password": "long-test-password",
        })
        self.assertEqual(response.headers["Location"], "/me")

    def test_profile_settings_follow_and_html_escaping(self):
        self.register("alice")
        self.assertNotIn(b"Open your diary", self.client.get("/").data)
        with self.app.app_context():
            player_id = db.session.scalar(select(Player.id).limit(1))
        self.client.post(f"/players/{player_id}/follow", data={"csrf_token": self.token()})
        self.assertIn(b"Following", self.client.get(f"/players/{player_id}").data)
        self.client.post(f"/matches/{self.match_id}/watchlist", data={"csrf_token": self.token()})
        profile = self.client.get("/u/alice")
        for label in (b"Profile", b"Diary", b"Watchlist", b"Likes", b"Friends"):
            self.assertIn(label, profile.data)
        watchlist = self.client.get("/u/alice?tab=watchlist")
        self.assertIn(b"Your watchlist", watchlist.data)
        self.assertIn(b'aria-current="page">Watchlist', watchlist.data)
        self.assertNotIn(b">Watchlist <span>", watchlist.data)
        self.assertIn(b'class="profile-section-total">1</small>', watchlist.data)
        response = self.client.post("/settings", data={
            "csrf_token": self.token(), "display_name": "Court Reader",
            "bio": "Grass-court fan",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Court Reader", self.client.get("/u/alice").data)
        portrait = BytesIO()
        Image.new("RGB", (48, 64), "#d6ed80").save(portrait, "PNG")
        portrait.seek(0)
        response = self.client.post("/settings", data={
            "csrf_token": self.token(), "display_name": "Court Reader",
            "bio": "Grass-court fan", "avatar": (portrait, "portrait.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"Settings saved", response.data)
        with self.app.app_context():
            self.assertIsNotNone(db.session.scalar(select(ProfileImage)))
        avatar = self.client.get("/u/alice/avatar")
        self.assertEqual(avatar.status_code, 200)
        self.assertEqual(avatar.mimetype, "image/webp")
        self.assertIn(b'/u/alice/avatar', self.client.get("/u/alice").data)
        response = self.client.post(f"/matches/{self.match_id}/log", data={
            "csrf_token": self.token(), "watched_on": "2025-01-30",
            "body": "<script>alert(1)</script>", "public": "on", "spoilers": "on",
        }, follow_redirects=True)
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertNotIn(b"<script>alert(1)</script>", response.data)
        self.assertIn(b"Review contains spoilers", self.client.get("/u/alice?tab=diary").data)
        with self.app.app_context():
            self.assertIsNone(db.session.scalar(select(WatchlistItem.id)))

    def test_account_export_and_deletion(self):
        self.register("alice")
        with self.app.app_context():
            player_id = db.session.scalar(select(Player.id).limit(1))
            another_match_id = db.session.scalar(
                select(Match.id).where(Match.id != self.match_id).limit(1)
            )
        self.client.post(f"/players/{player_id}/follow", data={"csrf_token": self.token()})
        self.client.post(f"/matches/{another_match_id}/watchlist", data={"csrf_token": self.token()})
        self.client.post(f"/matches/{self.match_id}/log", data={
            "csrf_token": self.token(), "watched_on": "2025-01-30",
            "rating": "9", "body": "Alice's private data", "public": "on",
        })
        with self.app.app_context():
            alice_review_id = db.session.scalar(select(Review.id))
        self.client.post("/logout", data={"csrf_token": self.token()})

        self.register("bob")
        self.client.post(f"/reviews/{alice_review_id}/comments", data={
            "csrf_token": self.token(), "body": "Bob's comment",
        })
        self.client.post(f"/matches/{another_match_id}/log", data={
            "csrf_token": self.token(), "watched_on": "2025-01-30", "public": "on",
        })
        with self.app.app_context():
            bob_review_id = db.session.scalar(
                select(Review.id).where(Review.match_id == another_match_id)
            )
            bob_comment_id = db.session.scalar(
                select(Comment.id).where(Comment.review_id == alice_review_id)
            )
        self.client.post("/logout", data={"csrf_token": self.token()})

        self.client.get("/login")
        self.client.post("/login", data={
            "csrf_token": self.token(), "identity": "alice", "password": "long-test-password",
        })
        self.assertIn(
            f'action="/comments/{bob_comment_id}/delete"'.encode(),
            self.client.get(f"/matches/{self.match_id}").data,
        )
        self.client.post(f"/reviews/{bob_review_id}/comments", data={
            "csrf_token": self.token(), "body": "Alice's comment",
        })
        export = self.client.get("/settings/export")
        self.assertEqual(export.status_code, 200)
        self.assertIn("no-store", export.headers["Cache-Control"])
        data = export.get_json()
        self.assertEqual(data["email"], "alice@example.com")
        self.assertEqual(data["diary"][0]["rating_out_of_ten"], 9)
        self.assertEqual(data["comments"][0]["body"], "Alice's comment")
        self.assertEqual(data["watchlist_match_ids"], [another_match_id])
        self.assertEqual(len(data["followed_players"]), 1)
        self.assertNotIn("password_hash", export.get_data(as_text=True))

        self.client.post("/settings/delete-account", data={
            "csrf_token": self.token(), "username": "alice", "password": "wrong-password",
        })
        with self.app.app_context():
            self.assertIsNotNone(db.session.scalar(select(User.id).where(User.username == "alice")))
        self.assertEqual(self.client.post("/settings/delete-account", data={
            "csrf_token": self.token(), "username": "alice", "password": "long-test-password",
        }).status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.scalar(select(User.id).where(User.username == "alice")))
            self.assertIsNone(db.session.scalar(select(Review.id).where(Review.id == alice_review_id)))
            self.assertIsNotNone(db.session.scalar(select(Review.id).where(Review.id == bob_review_id)))
            self.assertIsNone(db.session.scalar(select(Comment.id)))
            self.assertIsNone(db.session.scalar(select(FollowedPlayer.id)))
            self.assertIsNone(db.session.scalar(select(WatchlistItem.id)))
        self.assertEqual(self.client.get("/settings/export").status_code, 302)

    def test_reports_are_moderated_by_verified_admin(self):
        self.app.config["ADMIN_EMAIL"] = "alice@example.com"
        self.register("alice")
        self.client.post(f"/matches/{self.match_id}/log", data={
            "csrf_token": self.token(), "watched_on": "2025-01-30",
            "body": "A public review", "public": "on",
        })
        with self.app.app_context():
            review_id = db.session.scalar(select(Review.id))
        self.client.post("/logout", data={"csrf_token": self.token()})
        self.register("bob")
        self.assertIn(b"Report", self.client.get(f"/matches/{self.match_id}").data)
        response = self.client.post("/reports", data={
            "csrf_token": self.token(), "target": "review",
            "target_id": str(review_id), "reason": "spam",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/moderation").status_code, 403)
        with self.app.app_context():
            report_id = db.session.scalar(select(Report.id))
        self.client.post("/logout", data={"csrf_token": self.token()})
        self.client.get("/login")
        self.client.post("/login", data={
            "csrf_token": self.token(), "identity": "alice", "password": "long-test-password",
        })
        self.assertIn(b"A public review", self.client.get("/moderation").data)
        self.client.post(f"/moderation/reports/{report_id}/remove", data={
            "csrf_token": self.token(),
        })
        with self.app.app_context():
            self.assertIsNone(db.session.scalar(select(Review.id)))
            self.assertIsNone(db.session.scalar(select(Report.id)))


if __name__ == "__main__":
    unittest.main()
