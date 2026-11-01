import hmac
import html
import io
import re
import unicodedata
from math import ceil
from functools import lru_cache
from datetime import date, timedelta
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape  # nosec B406: output escaping only; no XML parsing

import requests
from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, login_required, login_user
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import and_, case, func, or_, select, union_all
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased, joinedload

from . import db
from .models import Comment, FollowedPlayer, LiveMatch, Match, Player, PlayerExternalId, PlayerPhoto, RankingSnapshot, Review, TournamentSubscription, User, WatchlistItem, utcnow
from .prize_money import update_prize_money
from .result_corrections import correction_for_match
from .security import client_ip, limit_action
from .identities import catalog_is_visible, match_identity_is_valid, profiles_for_matches
from .stats import community_statistics, percent, player_match_scope, player_statistics
from .pagination import bounded_page
from .discussions import discussion_previews
from .snapshot_policy import current_snapshot_condition
from .rankings import latest_singles_dates, valid_singles_ranking
from .tournament_catalog import tournament_level, tournament_profile, tournament_slug
from .web import public_url, schema_table_exists, shared_cache, xml_response
from .live_routes import register_live_routes
from .auth_routes import register_auth_routes
from .news_routes import register_news_routes
from .profile_routes import register_profile_routes
from .moderation_routes import register_moderation_routes
from .social_routes import register_social_routes
from .community_routes import register_community_routes

site = Blueprint("site", __name__)
SITEMAP_PAGE_SIZE = 20_000
SITEMAP_START_DATE = date(2020, 1, 1)
# Keep search-engine discovery focused on the tour levels people are most
# likely to search for. Lower-level results remain available inside Tennisd.
SITEMAP_LEVELS = ("G", "M", "PM", "P", "A", "F")


def match_location(match):
    return tournament_profile(match.tournament).get("location", "Location unavailable")


def resolve_tournament_name(tour, slug):
    tour = tour.upper()
    if tour not in ("ATP", "WTA"):
        return None
    names = db.session.scalars(
        select(Match.tournament).where(Match.tour == tour).distinct()
    ).all()
    return next((name for name in names if tournament_slug(name) == slug), None)


def current_match_rows(status, *, now=None):
    """Return an empty slate while a new deployment is waiting for its migration."""
    if not schema_table_exists(LiveMatch.__tablename__):
        return []
    statement = select(LiveMatch).where(LiveMatch.status == status, current_snapshot_condition(now))
    if status in ("finished", "cancelled"):
        statement = statement.order_by(LiveMatch.finished_at.desc(), LiveMatch.starts_at.desc()).limit(24)
    else:
        statement = statement.order_by(LiveMatch.starts_at, LiveMatch.tournament, LiveMatch.provider_id)
    return db.session.scalars(statement).all()


def current_match_players(matches):
    """Match live-feed participants to permanent profiles by provider ID or name."""
    return profiles_for_matches(matches)


def archived_match_id_for_live(match):
    """Find the permanent result that represents a live-feed row."""
    exact = db.session.scalar(
        select(Match.id).where(Match.provider_id == f"livetennisapi:{match.provider_id}")
    )
    if exact or not match.starts_at or match.is_doubles:
        return exact

    player_pair = current_match_players([match]).get(match.provider_id, (None, None))
    if not all(player_pair):
        return None
    participants = or_(
        and_(Match.winner_id == player_pair[0].id, Match.loser_id == player_pair[1].id),
        and_(Match.winner_id == player_pair[1].id, Match.loser_id == player_pair[0].id),
    )
    matches = db.session.scalars(
        select(Match.id).where(
            catalog_is_visible(),
            Match.tour == match.tour,
            Match.week_start == match.starts_at.date(),
            func.lower(Match.tournament) == match.tournament.lower(),
            participants,
        ).limit(2)
    ).all()
    return matches[0] if len(matches) == 1 else None


def player_live_matches(player, limit=12):
    if not schema_table_exists(LiveMatch.__tablename__):
        return []
    external_ids = db.session.scalars(
        select(PlayerExternalId.external_id).where(
            PlayerExternalId.player_id == player.id,
            PlayerExternalId.provider == "livetennisapi",
        )
    ).all()
    conditions = [
        func.lower(LiveMatch.player1_name) == player.name.casefold(),
        func.lower(LiveMatch.player2_name) == player.name.casefold(),
    ]
    if external_ids:
        conditions.extend((
            LiveMatch.player1_provider_id.in_(external_ids),
            LiveMatch.player2_provider_id.in_(external_ids),
        ))
    status_order = case(
        (LiveMatch.status == "live", 0),
        (LiveMatch.status == "upcoming", 1),
        (LiveMatch.status == "finished", 2),
        else_=3,
    )
    matches = db.session.scalars(
        select(LiveMatch)
        .where(
            LiveMatch.is_doubles.is_(False),
            LiveMatch.tour == player.tour,
            current_snapshot_condition(),
            or_(*conditions),
        )
        .order_by(status_order, LiveMatch.starts_at.desc())
        .limit(limit)
    ).all()
    profiles = profiles_for_matches(matches)
    matched = [
        match for match in matches
        if any(profile is not None and profile.id == player.id for profile in profiles[match.provider_id])
    ]
    return [
        match for match in matched
        if not (
            match.status in ("finished", "verifying")
            and archived_match_id_for_live(match)
        )
    ]


@lru_cache(maxsize=512)
def wikimedia_player_photo(wikidata_id):
    if not wikidata_id or not re.fullmatch(r"Q[1-9][0-9]*", wikidata_id):
        return None
    try:
        response = requests.get(
            f"https://www.wikidata.org/wiki/Special:EntityData/{wikidata_id}.json",
            headers={"Accept": "application/json", "User-Agent": "Tennisd/1.0 (player portraits)"},
            timeout=4,
        )
        response.raise_for_status()
        claim = response.json()["entities"][wikidata_id]["claims"]["P18"][0]
        filename = claim["mainsnak"]["datavalue"]["value"]
        return f"https://commons.wikimedia.org/wiki/Special:Redirect/file/{quote(filename, safe='')}?width=420"
    except (KeyError, IndexError, TypeError, ValueError, requests.RequestException):
        return None


def normalized_person_name(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(character for character in value if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


@lru_cache(maxsize=2048)
def wikipedia_player_photo(name):
    """Find a public Wikipedia thumbnail when the catalog has no Wikidata link."""
    if not name or len(name) > 120:
        return None
    try:
        response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "generator": "search",
                "gsrsearch": f'intitle:"{name}" tennis', "gsrnamespace": 0,
                "gsrlimit": 5, "prop": "pageimages", "piprop": "thumbnail",
                "pithumbsize": 420, "format": "json",
            },
            headers={"User-Agent": "Tennisd/1.0 (player portraits)"}, timeout=4,
        )
        response.raise_for_status()
        target = normalized_person_name(name)
        pages = (response.json().get("query") or {}).get("pages") or {}
        for page in pages.values():
            title = re.sub(r"\s*\([^)]*\)\s*$", "", page.get("title", ""))
            if normalized_person_name(title) == target:
                source = (page.get("thumbnail") or {}).get("source")
                if source and source.startswith((
                    "https://upload.wikimedia.org/", "https://thumb.wikimedia.org/"
                )):
                    return source
    except (AttributeError, TypeError, ValueError, requests.RequestException):
        pass
    return None


@lru_cache(maxsize=2048)
def wikidata_search_player_photo(name):
    """Find portraits missed by exact Wikipedia title matching."""
    if not name or len(name) > 120:
        return None
    try:
        response = requests.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbsearchentities", "search": name, "language": "en",
                "uselang": "en", "type": "item", "limit": 6, "format": "json",
            },
            headers={"User-Agent": "Tennisd/1.0 (player portraits)"}, timeout=4,
        )
        response.raise_for_status()
        target = normalized_person_name(name)
        for item in response.json().get("search", []):
            label = normalized_person_name(item.get("label", ""))
            description = (item.get("description") or "").casefold()
            if label == target and "tennis" in description:
                photo = wikimedia_player_photo(item.get("id"))
                if photo:
                    return photo
    except (AttributeError, TypeError, ValueError, requests.RequestException):
        pass
    return None


def public_player_photo(player):
    stored = player.primary_photo_url
    if stored:
        return stored
    discovered = (
        wikimedia_player_photo(player.wikidata_id)
        or wikipedia_player_photo(player.name)
        or wikidata_search_player_photo(player.name)
    )
    if not discovered:
        return None
    # Serverless instances do not share their in-memory lookup cache. Persisting a
    # successful public portrait makes every later request a cheap database read.
    try:
        existing = db.session.scalar(
            select(PlayerPhoto.id).where(
                PlayerPhoto.player_id == player.id,
                PlayerPhoto.url == discovered,
            )
        )
        if existing is None:
            db.session.add(PlayerPhoto(
                player_id=player.id,
                url=discovered,
                is_primary=True,
            ))
            db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
    return discovered


def player_placeholder(name, max_age=86400):
    initials = html.escape("".join(part[0] for part in name.split()[:2]).upper())
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="420" height="560" viewBox="0 0 420 560"><defs><linearGradient id="g" x2="0" y2="1"><stop stop-color="#335b48"/><stop offset="1" stop-color="#13271f"/></linearGradient></defs><rect width="420" height="560" fill="url(#g)"/><circle cx="210" cy="190" r="82" fill="#9fb4a4" opacity=".38"/><path d="M70 560c8-150 65-226 140-226s132 76 140 226" fill="#9fb4a4" opacity=".38"/><text x="210" y="305" text-anchor="middle" fill="#d6ed80" font-family="Arial,sans-serif" font-size="64" font-weight="700">{initials}</text></svg>'''
    response = Response(svg, mimetype="image/svg+xml")
    response.cache_control.public = True
    response.cache_control.max_age = max_age
    response.headers["Vercel-CDN-Cache-Control"] = f"max-age={max_age}"
    return response


def prepare_profile_image(upload):
    raw = upload.read(1_500_001)
    if not raw or len(raw) > 1_500_000:
        raise ValueError("Choose an image smaller than 1.5 MB.")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width > 4096 or source.height > 4096:
                raise ValueError("The image dimensions are too large.")
            source.seek(0)
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((640, 640), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, "WEBP", quality=84, method=6)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
        raise ValueError("Choose a valid JPG, PNG or WebP image.") from None
    data = output.getvalue()
    if len(data) > 500_000:
        raise ValueError("The processed image is still too large.")
    return data


@site.before_app_request
def check_csrf():
    if request.method == "POST":
        submitted = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not hmac.compare_digest(submitted, expected):
            abort(400, "Your form expired. Reload the page and try again.")
        limit_action("post-ip", client_ip(), 120, 60)


def sign_in_user(user):
    """Create a persistent login that remains revocable via session_version."""
    session.clear()
    session.permanent = True
    login_user(user, remember=True)


def sitemap_match_conditions():
    return (
        Match.week_start >= SITEMAP_START_DATE,
        Match.level.in_(SITEMAP_LEVELS),
        catalog_is_visible(),
    )


@site.get("/google872d566cb03fdad0.html")
def google_site_verification():
    return shared_cache(Response(
        "google-site-verification: google872d566cb03fdad0.html\n",
        content_type="text/html; charset=utf-8",
    ), browser_seconds=86400, cdn_seconds=604800, stale_seconds=604800)


@site.get("/google4394fddbd7a3b94e.html")
def google_search_console_verification():
    return shared_cache(Response(
        "google-site-verification: google4394fddbd7a3b94e.html\n",
        content_type="text/html; charset=utf-8",
    ), browser_seconds=86400, cdn_seconds=604800, stale_seconds=604800)


@site.get("/.well-known/security.txt")
def security_txt():
    body = "\n".join((
        "Contact: https://github.com/Snowstormme/tennisd/security/advisories/new",
        "Contact: mailto:notsnowstorm@proton.me",
        "Expires: 2027-10-31T00:00:00.000Z",
        "Preferred-Languages: en, ru",
        "Policy: https://github.com/Snowstormme/tennisd/blob/main/SECURITY.md",
        "Canonical: https://tennisd.onrender.com/.well-known/security.txt",
        "",
    ))
    return shared_cache(
        Response(body, content_type="text/plain; charset=utf-8"),
        browser_seconds=3600, cdn_seconds=86400, stale_seconds=86400,
    )


@site.get("/ads.txt")
def ads_txt():
    client_id = current_app.config.get("ADSENSE_CLIENT_ID", "")
    if not re.fullmatch(r"ca-pub-\d{16}", client_id):
        abort(404)
    response = Response(
        f"google.com, {client_id.removeprefix('ca-')}, DIRECT, f08c47fec0942fa0\n",
        content_type="text/plain; charset=utf-8",
    )
    return shared_cache(
        response, browser_seconds=3600, cdn_seconds=86400, stale_seconds=86400,
    )


@site.get("/<key>.txt")
def indexnow_key(key):
    configured = current_app.config.get("INDEXNOW_KEY", "")
    if not configured or not hmac.compare_digest(key, configured):
        abort(404)
    response = Response(f"{configured}\n", content_type="text/plain; charset=utf-8")
    return shared_cache(
        response, browser_seconds=86400, cdn_seconds=604800,
        stale_seconds=604800,
    )


@site.get("/robots.txt")
def robots_txt():
    body = "\n".join((
        "User-agent: GPTBot",
        "Disallow: /",
        "User-agent: ClaudeBot",
        "Disallow: /",
        "User-agent: CCBot",
        "Disallow: /",
        "User-agent: Bytespider",
        "Disallow: /",
        "User-agent: AhrefsBot",
        "Disallow: /",
        "User-agent: SemrushBot",
        "Disallow: /",
        "User-agent: MJ12bot",
        "Disallow: /",
        "User-agent: *",
        "Allow: /",
        "Disallow: /api/",
        "Disallow: /players/*/photo",
        "Disallow: /live-matches/*/players/",
        "Disallow: /*?",
        "Disallow: /settings",
        "Disallow: /notifications",
        "Disallow: /moderation",
        "Disallow: /login",
        "Disallow: /register",
        f"Sitemap: {public_url('/sitemap.xml')}",
        "",
    ))
    return shared_cache(
        Response(body, content_type="text/plain; charset=utf-8"),
        browser_seconds=3600, cdn_seconds=86400, stale_seconds=86400,
    )


@site.get("/sitemap.xml")
def sitemap_index():
    match_count = db.session.scalar(
        select(func.count(Match.id)).where(*sitemap_match_conditions())
    ) or 0
    locations = [
        public_url("/sitemap-core.xml"),
        public_url("/sitemap-players.xml"),
        public_url("/sitemap-tournaments.xml"),
        public_url("/sitemap-tournament-editions.xml"),
    ]
    locations.extend(
        public_url(f"/sitemap-matches-{page}.xml")
        for page in range(1, ceil(match_count / SITEMAP_PAGE_SIZE) + 1)
    )
    entries = "".join(f"<sitemap><loc>{xml_escape(url)}</loc></sitemap>" for url in locations)
    return xml_response(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</sitemapindex>"
    )


@site.get("/sitemap-core.xml")
def sitemap_core():
    endpoints = (
        "site.home", "site.matches", "site.players", "site.tournaments",
        "site.news", "site.about", "site.privacy",
    )
    entries = "".join(
        f"<url><loc>{xml_escape(public_url(url_for(endpoint)))}</loc></url>"
        for endpoint in endpoints
    )
    return xml_response(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )


@site.get("/sitemap-players.xml")
def sitemap_players():
    eligible_players = union_all(
        select(Match.winner_id.label("player_id")).where(*sitemap_match_conditions()),
        select(Match.loser_id.label("player_id")).where(*sitemap_match_conditions()),
    ).subquery()
    player_ids = db.session.scalars(
        select(eligible_players.c.player_id).distinct().order_by(eligible_players.c.player_id)
    ).all()
    entries = "".join(
        f"<url><loc>{xml_escape(public_url(url_for('site.player_detail', player_id=player_id)))}</loc></url>"
        for player_id in player_ids
    )
    return xml_response(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )


@site.get("/sitemap-tournaments.xml")
def sitemap_tournaments():
    tournaments = db.session.execute(
        select(Match.tour, Match.tournament)
        .where(*sitemap_match_conditions())
        .distinct().order_by(Match.tour, Match.tournament)
    ).all()
    entries = "".join(
        f"<url><loc>{xml_escape(public_url(url_for('site.tournament_detail', tour=tour.lower(), slug=tournament_slug(name))))}</loc></url>"
        for tour, name in tournaments
    )
    return xml_response(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )


@site.get("/sitemap-tournament-editions.xml")
def sitemap_tournament_editions():
    editions = db.session.execute(
        select(
            Match.tour,
            Match.tournament,
            func.extract("year", Match.week_start).label("season"),
            func.max(Match.week_start).label("last_match_week"),
        )
        .where(*sitemap_match_conditions())
        .group_by(Match.tour, Match.tournament, func.extract("year", Match.week_start))
        .order_by(Match.tour, Match.tournament, func.extract("year", Match.week_start))
    ).all()
    entries = "".join(
        f"<url><loc>{xml_escape(public_url(url_for('site.tournament_edition_detail', tour=tour.lower(), slug=tournament_slug(name), season=int(season))))}</loc>"
        f"<lastmod>{last_match_week.isoformat()}</lastmod></url>"
        for tour, name, season, last_match_week in editions
    )
    return xml_response(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )


@site.get("/sitemap-matches-<int:page>.xml")
def sitemap_matches(page):
    if page < 1:
        abort(404)
    rows = db.session.execute(
        select(Match.id, Match.week_start)
        .where(*sitemap_match_conditions())
        .order_by(Match.id)
        .offset((page - 1) * SITEMAP_PAGE_SIZE).limit(SITEMAP_PAGE_SIZE)
    ).all()
    if not rows:
        abort(404)
    entries = "".join(
        f"<url><loc>{xml_escape(public_url(url_for('site.match_detail', match_id=match_id)))}</loc>"
        f"<lastmod>{week_start.isoformat()}</lastmod></url>"
        for match_id, week_start in rows
    )
    return xml_response(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )


@site.get("/")
def home():
    winner = aliased(Player)
    loser = aliased(Player)
    winner_has_stored_photo = select(PlayerPhoto.id).where(
        PlayerPhoto.player_id == winner.id
    ).exists()
    loser_has_stored_photo = select(PlayerPhoto.id).where(
        PlayerPhoto.player_id == loser.id
    ).exists()
    recent_matches = db.session.scalars(
        select(Match)
        .join(winner, Match.winner_id == winner.id)
        .join(loser, Match.loser_id == loser.id)
        .where(
            catalog_is_visible(),
            Match.level.in_(("G", "M", "PM", "P", "A", "I", "F")),
            winner_has_stored_photo,
            loser_has_stored_photo,
        )
        .options(joinedload(Match.winner), joinedload(Match.loser))
        # This order follows ix_match_archive_browse and avoids sorting the
        # complete growing archive for every home-page request.
        .order_by(Match.week_start.desc(), Match.tournament, Match.id).limit(6)
    ).all()
    featured_matches = db.session.scalars(
        select(Match)
        .join(winner, Match.winner_id == winner.id)
        .join(loser, Match.loser_id == loser.id)
        .where(
            catalog_is_visible(),
            Match.level.in_(("G", "M", "PM", "P", "A", "I", "F")),
            or_(winner.wikidata_id.is_not(None), winner_has_stored_photo),
            or_(loser.wikidata_id.is_not(None), loser_has_stored_photo),
        )
        .options(joinedload(Match.winner), joinedload(Match.loser))
        .order_by(
            func.coalesce(Match.completed_at, Match.scheduled_at).desc(),
            Match.week_start.desc(), Match.id.desc(),
        ).limit(5)
    ).all()
    # Keep the homepage hero focused on permanent results. Live and upcoming
    # feed rows remain available in their dedicated Matches views.
    featured_live_matches = []
    featured_live_pairs = {}
    recent_reviews = db.session.scalars(
        select(Review).join(Match).where(Review.is_public.is_(True), catalog_is_visible())
        .options(joinedload(Review.user), joinedload(Review.match))
        .order_by(Review.created_at.desc()).limit(4)
    ).all()
    return render_template(
        "home.html", featured_matches=featured_matches, recent_matches=recent_matches,
        featured_live_matches=featured_live_matches,
        featured_live_pairs=featured_live_pairs,
        recent_reviews=recent_reviews, match_location=match_location,
    )


@site.get("/players/<player_id>/photo")
def player_photo(player_id):
    player = db.get_or_404(Player, player_id)
    photo_url = public_player_photo(player)
    if photo_url:
        response = redirect(photo_url)
        return shared_cache(
            response, browser_seconds=604800, cdn_seconds=2592000,
            stale_seconds=604800,
        )
    return player_placeholder(player.name)


@site.get("/live-matches/<provider_id>/players/<int:side>/photo")
def live_player_photo(provider_id, side):
    if side not in (1, 2) or not schema_table_exists(LiveMatch.__tablename__):
        abort(404)
    match = db.get_or_404(LiveMatch, provider_id)
    name = match.player1_name if side == 1 else match.player2_name
    photo_url = None
    if "/" not in name:
        photo_url = wikipedia_player_photo(name) or wikidata_search_player_photo(name)
    if photo_url:
        response = redirect(photo_url)
        return shared_cache(
            response, browser_seconds=604800, cdn_seconds=2592000,
            stale_seconds=604800,
        )
    return player_placeholder(name)


@site.get("/u/<username>/avatar")
def profile_avatar(username):
    user = db.session.scalar(
        select(User).where(User.username == username).options(joinedload(User.profile_image))
    )
    if user is None or user.profile_image is None:
        abort(404)
    image = user.profile_image
    return send_file(
        io.BytesIO(image.image_data), mimetype=image.mime_type,
        etag=f"avatar-{user.id}-{int(image.updated_at.timestamp())}", max_age=86400,
    )


@site.get("/matches")
def matches():
    view = request.args.get("view", "archive")
    if view not in ("archive", "live", "upcoming", "finished"):
        view = "archive"
    query = request.args.get("q", "").strip()[:80]
    tour = request.args.get("tour", "")
    surface = request.args.get("surface", "")
    year = request.args.get("year", "")
    level = request.args.get("level", "")
    order = request.args.get("order", "newest")
    pagination = None
    current_matches = []
    live_players = {}
    if view != "archive":
        current_matches = current_match_rows(view)
        live_players = current_match_players(current_matches)
        return render_template(
            "matches.html", view=view, pagination=pagination, current_matches=current_matches,
            live_players=live_players, query=query, tour=tour, surface=surface,
            year=year, level=level, order=order, match_location=match_location,
        )

    statement = select(Match).where(catalog_is_visible()).options(joinedload(Match.winner), joinedload(Match.loser))
    if query:
        winner = aliased(Player)
        loser = aliased(Player)
        statement = statement.join(winner, Match.winner).join(loser, Match.loser).where(
            or_(
                Match.tournament.ilike(f"%{query}%"),
                winner.name.ilike(f"%{query}%"),
                loser.name.ilike(f"%{query}%"),
            )
        )
    if tour in ("ATP", "WTA"):
        statement = statement.where(Match.tour == tour)
    if surface in ("Hard", "Clay", "Grass", "Carpet"):
        statement = statement.where(Match.surface == surface)
    if level in ("G", "M", "PM", "P", "A", "I", "F", "O", "D"):
        statement = statement.where(Match.level == level)
    if len(year) == 4 and year.isascii() and year.isdigit() and 1968 <= int(year) <= date.today().year:
        statement = statement.where(func.extract("year", Match.week_start) == int(year))
    statement = statement.order_by(
        Match.week_start.asc() if order == "oldest" else Match.week_start.desc(),
        Match.tournament, Match.id,
    )
    pagination = bounded_page(statement, page=request.args.get("page", 1, type=int), per_page=18)
    return render_template(
        "matches.html", view=view, pagination=pagination, current_matches=current_matches,
        live_players=live_players, query=query, tour=tour,
        surface=surface, year=year, level=level, order=order, match_location=match_location, current_year=date.today().year,
    )


@site.get("/matches/today")
def matches_today():
    return redirect(url_for("site.matches"), code=301)


@site.get("/matches/this-week")
def matches_this_week():
    return redirect(url_for("site.matches"), code=301)


@site.get("/matches/top-rated")
def top_rated_matches():
    return redirect(url_for("site.matches"), code=301)


@site.get("/matches/<path:match_id>")
def match_detail(match_id):
    match = db.get_or_404(Match, match_id)
    review_page = bounded_page(
        select(Review).where(Review.match_id == match_id, Review.is_public.is_(True))
        .options(joinedload(Review.user))
        .order_by(Review.created_at.desc(), Review.id.desc()),
        page=request.args.get("page", 1, type=int),
    )
    reviews = review_page.items
    comment_previews, comment_totals = discussion_previews([review.id for review in reviews])
    own_review = None
    on_watchlist = False
    if current_user.is_authenticated:
        own_review = db.session.scalar(
            select(Review).where(Review.match_id == match_id, Review.user_id == current_user.id)
        )
        on_watchlist = db.session.scalar(
            select(WatchlistItem.id).where(
                WatchlistItem.match_id == match_id, WatchlistItem.user_id == current_user.id
            )
        ) is not None
    serve_stats = []
    if match.w_ace is not None and match.l_ace is not None:
        serve_stats.append(("Aces", match.w_ace, match.l_ace, None))
    if match.w_df is not None and match.l_df is not None:
        serve_stats.append(("Double faults", match.w_df, match.l_df, None))
    if match.w_svpt and match.l_svpt and match.w_first_in is not None and match.l_first_in is not None:
        serve_stats.append((
            "First serve in", percent(match.w_first_in, match.w_svpt),
            percent(match.l_first_in, match.l_svpt), "%",
        ))
    if match.w_first_in and match.l_first_in and match.w_first_won is not None and match.l_first_won is not None:
        serve_stats.append((
            "First serve points won", percent(match.w_first_won, match.w_first_in),
            percent(match.l_first_won, match.l_first_in), "%",
        ))
    return render_template(
        "match.html", match=match, reviews=reviews, own_review=own_review,
        review_page=review_page, comment_previews=comment_previews, comment_totals=comment_totals,
        community=community_statistics(match), serve_stats=serve_stats,
        today=date.today(), on_watchlist=on_watchlist, match_location=match_location,
        data_warning=not match_identity_is_valid(match),
        verified_correction=correction_for_match(match),
    )


@site.post("/matches/<path:match_id>/watchlist")
@login_required
def toggle_watchlist(match_id):
    db.get_or_404(Match, match_id)
    item = db.session.scalar(
        select(WatchlistItem).where(
            WatchlistItem.user_id == current_user.id, WatchlistItem.match_id == match_id
        )
    )
    if item:
        db.session.delete(item)
        flash("Removed from your watchlist.", "success")
    else:
        db.session.add(WatchlistItem(user_id=current_user.id, match_id=match_id))
        flash("Saved to your watchlist.", "success")
    db.session.commit()
    return redirect(url_for("site.match_detail", match_id=match_id))


@site.get("/players")
def players():
    query = request.args.get("q", "").strip()[:80]
    tour = request.args.get("tour", "")
    view = request.args.get("view", "players")
    if view not in ("players", "rankings"):
        view = "players"
    appearances = union_all(
        select(Match.winner_id.label("player_id")).where(catalog_is_visible()),
        select(Match.loser_id.label("player_id")).where(catalog_is_visible()),
    ).subquery()
    match_counts = select(
        appearances.c.player_id, func.count().label("match_count")
    ).group_by(appearances.c.player_id).subquery()
    follower_counts = select(
        FollowedPlayer.player_id, func.count().label("follower_count")
    ).group_by(FollowedPlayer.player_id).subquery()
    if view == "rankings":
        latest_dates = latest_singles_dates()
        statement = select(RankingSnapshot).options(joinedload(RankingSnapshot.player)).join(
            latest_dates, latest_dates.c.player_id == RankingSnapshot.player_id
        ).join(Player, Player.id == RankingSnapshot.player_id).where(
            RankingSnapshot.ranked_on == latest_dates.c.ranked_on, valid_singles_ranking(),
        )
        if query:
            statement = statement.where(Player.name.ilike(f"%{query}%"))
        if tour in ("ATP", "WTA"):
            statement = statement.where(Player.tour == tour)
        statement = statement.order_by(RankingSnapshot.rank, Player.tour, Player.name, Player.id)
    else:
        statement = (
            select(Player).where(func.lower(Player.name).not_in(("unknown player", "unknown", "tbd", "tba")))
            .outerjoin(match_counts, match_counts.c.player_id == Player.id)
            .outerjoin(follower_counts, follower_counts.c.player_id == Player.id)
        )
        if query:
            statement = statement.where(Player.name.ilike(f"%{query}%"))
        if tour in ("ATP", "WTA"):
            statement = statement.where(Player.tour == tour)
        statement = statement.order_by(
            func.coalesce(follower_counts.c.follower_count, 0).desc(),
            func.coalesce(match_counts.c.match_count, 0).desc(),
            Player.wikidata_id.is_(None), Player.name, Player.id,
        )
    pagination = bounded_page(statement, page=request.args.get("page", 1, type=int), per_page=30)
    player_ids = [
        item.player_id if view == "rankings" else item.id
        for item in pagination.items
    ]
    metrics = {player_id: {"matches": 0, "followers": 0, "rank": None} for player_id in player_ids}
    if player_ids:
        for player_id, count in db.session.execute(
            select(match_counts.c.player_id, match_counts.c.match_count)
            .where(match_counts.c.player_id.in_(player_ids))
        ):
            metrics[player_id]["matches"] = count
        for player_id, count in db.session.execute(
            select(follower_counts.c.player_id, follower_counts.c.follower_count)
            .where(follower_counts.c.player_id.in_(player_ids))
        ):
            metrics[player_id]["followers"] = count
        latest_dates = latest_singles_dates(player_ids)
        for player_id, rank in db.session.execute(
            select(RankingSnapshot.player_id, RankingSnapshot.rank).join(
                latest_dates,
                (latest_dates.c.player_id == RankingSnapshot.player_id)
                & (latest_dates.c.ranked_on == RankingSnapshot.ranked_on),
            ).where(valid_singles_ranking())
        ):
            metrics[player_id]["rank"] = rank
    return render_template(
        "players.html", pagination=pagination, query=query, tour=tour, view=view,
        player_metrics=metrics,
    )


@site.get("/rankings")
def rankings():
    args = request.args.to_dict(flat=True)
    args["view"] = "rankings"
    return redirect(url_for("site.players", **args), code=301)


@site.get("/tournaments")
def tournaments():
    selected = request.args.get("event", "")[:180]
    if "|" in selected:
        selected_tour, selected_slug = selected.split("|", 1)
        if selected_tour in ("ATP", "WTA") and selected_slug:
            return redirect(url_for(
                "site.tournament_detail", tour=selected_tour.lower(), slug=selected_slug,
            ))
    query = request.args.get("q", "").strip()[:80]
    tour = request.args.get("tour", "")
    surface = request.args.get("surface", "")
    level = request.args.get("level", "")
    statement = select(
        Match.tournament, Match.surface, Match.tour, Match.level,
        func.count(Match.id).label("matches"),
        func.min(Match.week_start).label("earliest"),
        func.max(Match.week_start).label("latest"),
    )
    if query:
        statement = statement.where(Match.tournament.ilike(f"%{query}%"))
    if tour in ("ATP", "WTA"):
        statement = statement.where(Match.tour == tour)
    if surface in ("Hard", "Clay", "Grass", "Carpet"):
        statement = statement.where(Match.surface == surface)
    if level in ("G", "M", "PM", "P", "A", "I", "F", "O", "D"):
        statement = statement.where(Match.level == level)
    rows = db.session.execute(
        statement.where(catalog_is_visible()).group_by(Match.tournament, Match.surface, Match.tour, Match.level)
        .order_by(func.max(Match.week_start).desc(), Match.tournament, Match.tour)
    ).all()
    grouped = {}
    for row in rows:
        key = (row.tour, row.tournament)
        item = grouped.setdefault(key, {
            "tournament": row.tournament, "tour": row.tour, "matches": 0,
            "earliest": row.earliest, "latest": row.latest, "surfaces": set(),
            "levels": set(), "slug": tournament_slug(row.tournament),
        })
        item["matches"] += row.matches
        item["earliest"] = min(item["earliest"], row.earliest)
        item["latest"] = max(item["latest"], row.latest)
        item["surfaces"].add(row.surface)
        item["levels"].add(row.level)
    tournament_rows = list(grouped.values())
    for item in tournament_rows:
        priority, value_label, value_group = tournament_level(item["levels"])
        item.update({
            "priority": priority, "value_label": value_label, "value_group": value_group,
            "profile": tournament_profile(item["tournament"]),
        })
    tournament_rows.sort(key=lambda item: (
        item["priority"], -item["latest"].toordinal(), item["tournament"], item["tour"],
    ))
    filtered = bool(query or tour or surface or level)
    featured = [] if filtered else [item for item in tournament_rows if item["priority"] == 0]
    all_remaining = tournament_rows if filtered else [item for item in tournament_rows if item["priority"] != 0]
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 36
    total = len(all_remaining)
    pages = max(1, ceil(total / per_page))
    if page > pages:
        page = pages
    remaining = all_remaining[(page - 1) * per_page:page * per_page]
    return render_template(
        "tournaments.html", tournaments=remaining, featured=featured,
        tournament_total=total, page=page, pages=pages,
        query=query, tour=tour, surface=surface, level=level,
        tournament_slug=tournament_slug,
    )


@site.get("/tournaments/<tour>/<slug>")
def tournament_detail(tour, slug):
    tour = tour.upper()
    tournament_name = resolve_tournament_name(tour, slug)
    if tournament_name is None:
        abort(404)
    condition = (Match.tour == tour, Match.tournament == tournament_name, catalog_is_visible())
    match_page = bounded_page(select(Match).where(*condition)
        .options(joinedload(Match.winner), joinedload(Match.loser))
        .order_by(Match.week_start.desc(), Match.id.desc()),
        page=request.args.get("page", 1, type=int))
    title_counts = (select(Match.winner_id.label("player_id"), func.count().label("titles"))
        .where(*condition, Match.round == "F").group_by(Match.winner_id).subquery())
    champions = [{"player": player, "titles": count} for player, count in db.session.execute(
        select(Player, title_counts.c.titles).join(title_counts, title_counts.c.player_id == Player.id)
        .order_by(title_counts.c.titles.desc(), Player.name, Player.id).limit(8))]
    years = [int(year) for year in db.session.scalars(select(func.extract("year", Match.week_start))
        .where(*condition).distinct().order_by(func.extract("year", Match.week_start).desc()))]
    surfaces = db.session.scalars(
        select(Match.surface).where(*condition).distinct().order_by(Match.surface)
    ).all()
    levels = db.session.scalars(
        select(Match.level).where(*condition).distinct().order_by(Match.level)
    ).all()
    _, value_label, value_group = tournament_level(levels)
    subscriptions_ready = schema_table_exists(TournamentSubscription.__tablename__)
    subscribed = False
    subscriber_count = 0
    if subscriptions_ready:
        subscriber_count = db.session.scalar(select(func.count(TournamentSubscription.id)).where(
            TournamentSubscription.tour == tour,
            TournamentSubscription.tournament == tournament_name,
        ))
        if current_user.is_authenticated:
            subscribed = db.session.scalar(select(TournamentSubscription.id).where(
                TournamentSubscription.user_id == current_user.id,
                TournamentSubscription.tour == tour,
                TournamentSubscription.tournament == tournament_name,
            )) is not None
    live_matches = db.session.scalars(select(LiveMatch).where(
        LiveMatch.tour == tour, func.lower(LiveMatch.tournament) == tournament_name.lower(),
        LiveMatch.status.in_(("live", "upcoming")), current_snapshot_condition(),
    ).order_by(case((LiveMatch.status == "live", 0), else_=1), LiveMatch.starts_at, LiveMatch.provider_id)).all() if schema_table_exists(LiveMatch.__tablename__) else []
    return render_template(
        "tournament.html", tournament=tournament_name, tour=tour,
        profile=tournament_profile(tournament_name), matches=match_page.items, match_page=match_page,
        total_matches=match_page.total,
        champions=champions, years=years, surfaces=surfaces,
        levels=levels, value_label=value_label, value_group=value_group,
        live_matches=live_matches, match_location=match_location,
        subscriptions_ready=subscriptions_ready, subscribed=subscribed,
        subscriber_count=subscriber_count,
    )


@site.get("/tournaments/<tour>/<slug>/<int:season>")
def tournament_edition_detail(tour, slug, season):
    tour = tour.upper()
    tournament_name = resolve_tournament_name(tour, slug)
    if tournament_name is None or not 1968 <= season <= date.today().year + 1:
        abort(404)
    condition = (Match.tour == tour, Match.tournament == tournament_name, catalog_is_visible(),
                 func.extract("year", Match.week_start) == season)
    round_counts = dict(db.session.execute(select(Match.round, func.count(Match.id))
        .where(*condition).group_by(Match.round)).all())
    if not round_counts:
        abort(404)
    round_order = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "RR", "R64", "R32", "R16", "QF", "SF", "BR", "F")
    round_counts = dict(sorted(round_counts.items(), key=lambda item:
        (round_order.index(item[0]) if item[0] in round_order else 99, item[0] or "")))
    selected_round = request.args.get("round", "")[:16]
    if selected_round == "__unavailable__" and "" in round_counts:
        round_filter = ""
    elif selected_round and selected_round in round_counts:
        round_filter = selected_round
    else:
        selected_round = ""
        round_filter = None
    statement = select(Match).where(*condition)
    if round_filter is not None:
        statement = statement.where(Match.round == round_filter)
    match_page = bounded_page(statement.options(joinedload(Match.winner), joinedload(Match.loser))
        .order_by(case({r: i for i, r in enumerate(round_order)}, value=Match.round, else_=99),
                  Match.round, Match.week_start, Match.id),page=request.args.get("page", 1, type=int))
    rounds = {r: [m for m in match_page.items if m.round == r]
              for r in round_counts if any(m.round == r for m in match_page.items)}
    final = db.session.scalar(select(Match).where(*condition, Match.round == "F")
        .options(joinedload(Match.winner),joinedload(Match.loser)).limit(1)) if round_counts.get("F") == 1 else None
    surfaces = db.session.scalars(select(Match.surface).where(*condition).distinct().order_by(Match.surface)).all()
    return render_template(
        "tournament_edition.html", tournament=tournament_name, tour=tour, season=season,
        matches=match_page.items, rounds=rounds, round_counts=round_counts, selected_round=selected_round,
        match_page=match_page, total_matches=sum(round_counts.values()),
        final=final, surface=" / ".join(surfaces),
        profile=tournament_profile(tournament_name), slug=slug, match_location=match_location,
    )


@site.post("/tournaments/<tour>/<slug>/subscribe")
@login_required
def toggle_tournament_subscription(tour, slug):
    if not schema_table_exists(TournamentSubscription.__tablename__):
        abort(503)
    tour = tour.upper()
    tournament_name = resolve_tournament_name(tour, slug)
    if tournament_name is None:
        abort(404)
    subscription = db.session.scalar(select(TournamentSubscription).where(
        TournamentSubscription.user_id == current_user.id,
        TournamentSubscription.tour == tour,
        TournamentSubscription.tournament == tournament_name,
    ))
    if subscription:
        db.session.delete(subscription)
        flash(f"You stopped following {tournament_name}.", "success")
    else:
        db.session.add(TournamentSubscription(
            user_id=current_user.id, tour=tour, tournament=tournament_name,
        ))
        flash(f"You are now following {tournament_name}.", "success")
    db.session.commit()
    return redirect(url_for("site.tournament_detail", tour=tour.lower(), slug=slug))


@site.get("/search")
def search():
    query = request.args.get("q", "").strip()[:80]
    players_found, matches_found, members, tournaments_found = [], [], [], []
    if query:
        players_found = db.session.scalars(
            select(Player).where(Player.name.ilike(f"%{query}%")).order_by(Player.name, Player.id).limit(15)
        ).all()
        winner, loser = aliased(Player), aliased(Player)
        matches_found = db.session.scalars(
            select(Match).join(winner, Match.winner).join(loser, Match.loser).where(catalog_is_visible()).where(or_(
                Match.tournament.ilike(f"%{query}%"), winner.name.ilike(f"%{query}%"),
                loser.name.ilike(f"%{query}%"),
            )).order_by(Match.week_start.desc(), Match.id).limit(9)
        ).all()
        tournaments_found = db.session.execute(
            select(Match.tour, Match.tournament).where(
                catalog_is_visible(), Match.tournament.ilike(f"%{query}%"),
            ).distinct().order_by(Match.tournament, Match.tour).limit(12)
        ).all()
        members = db.session.scalars(
            select(User).where(or_(User.username.ilike(f"%{query}%"), User.display_name.ilike(f"%{query}%")))
            .order_by(User.username, User.id).limit(15)
        ).all()
    return render_template(
        "search.html", query=query, players=players_found, matches=matches_found,
        members=members, tournaments_found=tournaments_found, tournament_slug=tournament_slug, match_location=match_location,
    )


@site.get("/players/<player_id>")
def player_detail(player_id):
    player = db.get_or_404(Player, player_id)
    update_prize_money(player)
    stats = player_statistics(player)
    match_page = bounded_page(select(Match).where(player_match_scope(player.id))
        .options(joinedload(Match.winner), joinedload(Match.loser))
        .order_by(Match.week_start.desc(), Match.id.desc()),
        page=request.args.get("page", 1, type=int))
    ranking_history = db.session.scalars(
        select(RankingSnapshot).where(
            RankingSnapshot.player_id == player.id,
            RankingSnapshot.ranking_type == "singles",
            RankingSnapshot.rank > 0,
        ).order_by(RankingSnapshot.ranked_on.desc(), RankingSnapshot.id.desc()).limit(12)
    ).all()
    best_rank = db.session.scalar(select(func.min(RankingSnapshot.rank)).where(
        RankingSnapshot.player_id == player.id,
        RankingSnapshot.ranking_type == "singles",
        RankingSnapshot.rank > 0,
    ))
    live_matches = player_live_matches(player)
    following = False
    if current_user.is_authenticated:
        following = db.session.scalar(
            select(FollowedPlayer.id).where(
                FollowedPlayer.user_id == current_user.id,
                FollowedPlayer.player_id == player.id,
            )
        ) is not None
    return render_template(
        "player.html", player=player, stats=stats, following=following,
        live_matches=live_matches, live_players=current_match_players(live_matches),
        match_location=match_location, ranking_history=ranking_history, best_rank=best_rank, match_page=match_page,
    )


@site.get("/about")
def about():
    return render_template("about.html")


@site.get("/privacy")
def privacy():
    return render_template("privacy.html")


register_live_routes(
    site,
    current_match_rows=current_match_rows,
    current_match_players=current_match_players,
    archived_match_id_for_live=archived_match_id_for_live,
    match_location=match_location,
)


register_auth_routes(site, sign_in_user=sign_in_user)
register_news_routes(site)
register_profile_routes(
    site,
    match_location=match_location,
    tournament_slug=tournament_slug,
    prepare_profile_image=prepare_profile_image,
    sign_in_user=sign_in_user,
)
register_moderation_routes(site)
register_social_routes(site)
register_community_routes(site)
