import hmac
import html
import io
import re
import secrets
import unicodedata
from math import ceil
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import escape as xml_escape

import requests
from flask import Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import and_, case, delete, func, inspect, or_, select, union_all
from sqlalchemy.orm import aliased, joinedload

from . import db
from .models import AuthState, AuthToken, Comment, FeedbackSubmission, FollowedPlayer, Friendship, LiveMatch, LiveOdds, Match, Player, PlayerExternalId, PlayerPhoto, Poll, PollOption, PollVote, ProfileImage, RankingSnapshot, Report, Review, TournamentSubscription, User, WatchlistItem, utcnow
from .news_feed import NEWS_SOURCES, curate_news_items, fetch_news_article, fetch_news_items
from .prize_money import update_prize_money
from .security import client_ip, limit_action, send_account_email, valid_token, valid_verification_code
from .stats import community_statistics, diary_statistics, percent, player_statistics
from .tournament_catalog import tournament_level, tournament_profile, tournament_slug

site = Blueprint("site", __name__)
SITEMAP_PAGE_SIZE = 20_000

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


def current_match_rows(status):
    """Return an empty slate while a new deployment is waiting for its migration."""
    if not inspect(db.engine).has_table(LiveMatch.__tablename__):
        return []
    statement = select(LiveMatch).where(LiveMatch.status == status)
    if status == "upcoming":
        statement = statement.where(
            LiveMatch.starts_at >= utcnow() - timedelta(hours=6),
            LiveMatch.starts_at <= utcnow() + timedelta(days=7),
        )
    if status in ("finished", "cancelled"):
        statement = statement.order_by(LiveMatch.finished_at.desc(), LiveMatch.starts_at.desc()).limit(24)
    else:
        statement = statement.order_by(LiveMatch.starts_at, LiveMatch.tournament, LiveMatch.provider_id)
    return db.session.scalars(statement).all()


def current_match_players(matches):
    """Match live-feed participants to permanent profiles by provider ID or name."""
    external_ids = {
        provider_id
        for match in matches
        for provider_id in (match.player1_provider_id, match.player2_provider_id)
        if provider_id
    }
    external_rows = db.session.scalars(
        select(PlayerExternalId)
        .where(
            PlayerExternalId.provider == "livetennisapi",
            PlayerExternalId.external_id.in_(external_ids),
        )
        .options(joinedload(PlayerExternalId.player))
    ).all() if external_ids else []
    by_external = {row.external_id: row.player for row in external_rows}
    names = {
        name.casefold()
        for match in matches
        for name in (match.player1_name, match.player2_name)
        if name
    }
    if not names:
        return {}
    players = db.session.scalars(
        select(Player).where(func.lower(Player.name).in_(names))
    ).all()
    by_name = {normalized_person_name(player.name): player for player in players}
    return {
        match.provider_id: (
            by_external.get(match.player1_provider_id) or by_name.get(normalized_person_name(match.player1_name)),
            by_external.get(match.player2_provider_id) or by_name.get(normalized_person_name(match.player2_name)),
        )
        for match in matches
    }


def archived_match_id_for_live(match):
    """Find the permanent result that represents a live-feed row."""
    exact = db.session.scalar(
        select(Match.id).where(Match.provider_id == f"livetennisapi:{match.provider_id}")
    )
    if exact or not match.starts_at or match.is_doubles:
        return exact

    player_pair = current_match_players([match]).get(match.provider_id, (None, None))
    if all(player_pair):
        participants = or_(
            and_(Match.winner_id == player_pair[0].id, Match.loser_id == player_pair[1].id),
            and_(Match.winner_id == player_pair[1].id, Match.loser_id == player_pair[0].id),
        )
        return db.session.scalar(
            select(Match.id).where(
                Match.tour == match.tour,
                Match.week_start == match.starts_at.date(),
                participants,
            ).limit(1)
        )

    winner = aliased(Player)
    loser = aliased(Player)
    names = or_(
        and_(
            func.lower(winner.name) == match.player1_name.casefold(),
            func.lower(loser.name) == match.player2_name.casefold(),
        ),
        and_(
            func.lower(winner.name) == match.player2_name.casefold(),
            func.lower(loser.name) == match.player1_name.casefold(),
        ),
    )
    return db.session.scalar(
        select(Match.id).join(winner, Match.winner).join(loser, Match.loser).where(
            Match.tour == match.tour,
            Match.week_start == match.starts_at.date(),
            names,
        ).limit(1)
    )


def current_match_odds(matches):
    """Return the newest display quote without requiring the migration during deploy."""
    if not matches or not inspect(db.engine).has_table(LiveOdds.__tablename__):
        return {}
    ids = [match.provider_id for match in matches]
    rows = db.session.scalars(
        select(LiveOdds).where(LiveOdds.live_match_id.in_(ids)).order_by(LiveOdds.fetched_at.desc())
    ).all()
    return {row.live_match_id: row for row in rows}


def player_live_matches(player, limit=12):
    if not inspect(db.engine).has_table(LiveMatch.__tablename__):
        return []
    external_ids = db.session.scalars(
        select(PlayerExternalId.external_id).where(
            PlayerExternalId.player_id == player.id,
            PlayerExternalId.provider == "livetennisapi",
        )
    ).all()
    identity = normalized_person_name(player.name)
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
            LiveMatch.status != "cancelled",
            or_(*conditions),
        )
        .order_by(status_order, LiveMatch.starts_at.desc())
        .limit(limit)
    ).all()
    # SQLite and some feeds compare accented names differently; provider IDs remain authoritative.
    matched = [
        match for match in matches
        if any((
            match.player1_provider_id in external_ids,
            match.player2_provider_id in external_ids,
            normalized_person_name(match.player1_name) == identity,
            normalized_person_name(match.player2_name) == identity,
        ))
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
    stored = db.session.scalar(
        select(PlayerPhoto.url).where(PlayerPhoto.player_id == player.id)
        .order_by(PlayerPhoto.is_primary.desc(), PlayerPhoto.id)
    )
    return stored or wikimedia_player_photo(player.wikidata_id) or wikipedia_player_photo(player.name) or wikidata_search_player_photo(player.name)


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


def safe_next(default="site.home"):
    target = request.args.get("next", "")
    parsed = urlsplit(target)
    return (
        target if target.startswith("/") and not target.startswith("//")
        and "\\" not in target and not parsed.scheme and not parsed.netloc
        else url_for(default)
    )


def sign_in_user(user):
    """Create a persistent login that remains revocable via session_version."""
    session.clear()
    session.permanent = True
    login_user(user, remember=True)


def public_url(path):
    return f"{current_app.config['PUBLIC_BASE_URL'].rstrip('/')}{path}"


def xml_response(body):
    return Response(body, content_type="application/xml; charset=utf-8")


@site.get("/google872d566cb03fdad0.html")
def google_site_verification():
    return Response(
        "google-site-verification: google872d566cb03fdad0.html\n",
        content_type="text/html; charset=utf-8",
    )


@site.get("/google4394fddbd7a3b94e.html")
def google_search_console_verification():
    return Response(
        "google-site-verification: google4394fddbd7a3b94e.html\n",
        content_type="text/html; charset=utf-8",
    )


@site.get("/robots.txt")
def robots_txt():
    body = "\n".join((
        "User-agent: *",
        "Allow: /",
        "Disallow: /settings",
        "Disallow: /notifications",
        "Disallow: /moderation",
        "Disallow: /login",
        "Disallow: /register",
        f"Sitemap: {public_url('/sitemap.xml')}",
        "",
    ))
    return Response(body, content_type="text/plain; charset=utf-8")


@site.get("/sitemap.xml")
def sitemap_index():
    match_count = db.session.scalar(select(func.count(Match.id))) or 0
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
    player_ids = db.session.scalars(select(Player.id).order_by(Player.id)).all()
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
        select(Match.tour, Match.tournament).distinct().order_by(Match.tour, Match.tournament)
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
        select(Match.id, Match.week_start).order_by(Match.id)
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
    recent_matches = db.session.scalars(
        select(Match).where(Match.level.in_(("G", "M", "PM", "P", "A", "I", "F")))
        .options(joinedload(Match.winner), joinedload(Match.loser))
        # This order follows ix_match_archive_browse and avoids sorting the
        # complete growing archive for every home-page request.
        .order_by(Match.week_start.desc(), Match.tournament, Match.id).limit(8)
    ).all()
    winner = aliased(Player)
    loser = aliased(Player)
    winner_has_stored_photo = select(PlayerPhoto.id).where(
        PlayerPhoto.player_id == winner.id
    ).exists()
    loser_has_stored_photo = select(PlayerPhoto.id).where(
        PlayerPhoto.player_id == loser.id
    ).exists()
    featured_matches = db.session.scalars(
        select(Match)
        .join(winner, Match.winner_id == winner.id)
        .join(loser, Match.loser_id == loser.id)
        .where(
            Match.level.in_(("G", "M", "PM", "P", "A", "I", "F")),
            or_(winner.wikidata_id.is_not(None), winner_has_stored_photo),
            or_(loser.wikidata_id.is_not(None), loser_has_stored_photo),
        )
        .options(joinedload(Match.winner), joinedload(Match.loser))
        .order_by(Match.week_start.desc(), Match.tournament, Match.id).limit(5)
    ).all()
    recent_reviews = db.session.scalars(
        select(Review).where(Review.is_public.is_(True))
        .options(joinedload(Review.user), joinedload(Review.match))
        .order_by(Review.created_at.desc()).limit(4)
    ).all()
    return render_template(
        "home.html", featured_matches=featured_matches, recent_matches=recent_matches,
        recent_reviews=recent_reviews, match_location=match_location,
    )


@site.get("/players/<player_id>/photo")
def player_photo(player_id):
    player = db.get_or_404(Player, player_id)
    photo_url = public_player_photo(player)
    if photo_url:
        response = redirect(photo_url)
        response.cache_control.public = True
        response.cache_control.max_age = 604800
        response.headers["Vercel-CDN-Cache-Control"] = "max-age=604800"
        return response
    return player_placeholder(player.name)


@site.get("/live-matches/<provider_id>/players/<int:side>/photo")
def live_player_photo(provider_id, side):
    if side not in (1, 2) or not inspect(db.engine).has_table(LiveMatch.__tablename__):
        abort(404)
    match = db.get_or_404(LiveMatch, provider_id)
    name = match.player1_name if side == 1 else match.player2_name
    photo_url = None
    if "/" not in name:
        photo_url = wikipedia_player_photo(name) or wikidata_search_player_photo(name)
    if photo_url:
        response = redirect(photo_url)
        response.cache_control.public = True
        response.cache_control.max_age = 604800
        response.headers["Vercel-CDN-Cache-Control"] = "max-age=604800"
        return response
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
        live_odds = current_match_odds(current_matches)
        return render_template(
            "matches.html", view=view, pagination=pagination, current_matches=current_matches,
            live_players=live_players, live_odds=live_odds, query=query, tour=tour, surface=surface,
            year=year, level=level, order=order, match_location=match_location,
        )

    statement = select(Match).options(joinedload(Match.winner), joinedload(Match.loser))
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
    if year.isdigit() and 1968 <= int(year) <= 2026:
        statement = statement.where(func.extract("year", Match.week_start) == int(year))
    statement = statement.order_by(
        Match.week_start.asc() if order == "oldest" else Match.week_start.desc(),
        Match.tournament,
    )
    page = max(1, request.args.get("page", 1, type=int))
    pagination = db.paginate(statement, page=page, per_page=18, error_out=False)
    return render_template(
        "matches.html", view=view, pagination=pagination, current_matches=current_matches,
        live_players=live_players, query=query, tour=tour,
        surface=surface, year=year, level=level, order=order, match_location=match_location,
    )


@site.get("/live-matches/<provider_id>")
def live_match_detail(provider_id):
    if not inspect(db.engine).has_table(LiveMatch.__tablename__):
        abort(404)
    match = db.get_or_404(LiveMatch, provider_id)
    archived = archived_match_id_for_live(match)
    if archived:
        return redirect(url_for("site.match_detail", match_id=archived))
    players = current_match_players([match]).get(match.provider_id, (None, None))
    odds = current_match_odds([match]).get(match.provider_id)
    return render_template("live_match.html", match=match, players=players, odds=odds)


@site.get("/api/live-matches")
def live_matches_api():
    matches = current_match_rows("live")
    odds = current_match_odds(matches)
    response = jsonify({
        "matches": [{
            "id": match.provider_id, "score": match.score,
            "server": match.server, "synced_at": match.synced_at.isoformat(),
            "odds": ({
                "player1_price": odds[match.provider_id].player1_price,
                "player2_price": odds[match.provider_id].player2_price,
                "player1_probability": odds[match.provider_id].player1_probability,
                "player2_probability": odds[match.provider_id].player2_probability,
                "updated_at": odds[match.provider_id].fetched_at.isoformat(),
            } if match.provider_id in odds else None),
        } for match in matches]
    })
    response.cache_control.no_store = True
    return response


@site.get("/matches/<path:match_id>")
def match_detail(match_id):
    match = db.get_or_404(Match, match_id)
    reviews = db.session.scalars(
        select(Review).where(Review.match_id == match_id, Review.is_public.is_(True))
        .options(joinedload(Review.user), joinedload(Review.comments).joinedload(Comment.user))
        .order_by(Review.created_at.desc())
    ).unique().all()
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
        community=community_statistics(match), serve_stats=serve_stats,
        today=date.today(), on_watchlist=on_watchlist, match_location=match_location,
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
        select(Match.winner_id.label("player_id")),
        select(Match.loser_id.label("player_id")),
    ).subquery()
    match_counts = select(
        appearances.c.player_id, func.count().label("match_count")
    ).group_by(appearances.c.player_id).subquery()
    follower_counts = select(
        FollowedPlayer.player_id, func.count().label("follower_count")
    ).group_by(FollowedPlayer.player_id).subquery()
    if view == "rankings":
        latest_dates = select(
            RankingSnapshot.player_id,
            func.max(RankingSnapshot.ranked_on).label("ranked_on"),
        ).group_by(RankingSnapshot.player_id).subquery()
        statement = select(RankingSnapshot).options(joinedload(RankingSnapshot.player)).join(
            latest_dates, latest_dates.c.player_id == RankingSnapshot.player_id
        ).join(Player, Player.id == RankingSnapshot.player_id).where(
            RankingSnapshot.ranked_on == latest_dates.c.ranked_on,
        )
        if query:
            statement = statement.where(Player.name.ilike(f"%{query}%"))
        if tour in ("ATP", "WTA"):
            statement = statement.where(Player.tour == tour)
        statement = statement.order_by(RankingSnapshot.rank, Player.tour, Player.name)
    else:
        statement = (
            select(Player)
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
            Player.wikidata_id.is_(None), Player.name,
        )
    pagination = db.paginate(
        statement, page=max(1, request.args.get("page", 1, type=int)),
        per_page=30, error_out=False,
    )
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
        latest_dates = select(
            RankingSnapshot.player_id,
            func.max(RankingSnapshot.ranked_on).label("ranked_on"),
        ).where(RankingSnapshot.player_id.in_(player_ids)).group_by(
            RankingSnapshot.player_id
        ).subquery()
        for player_id, rank in db.session.execute(
            select(RankingSnapshot.player_id, RankingSnapshot.rank).join(
                latest_dates,
                (latest_dates.c.player_id == RankingSnapshot.player_id)
                & (latest_dates.c.ranked_on == RankingSnapshot.ranked_on),
            )
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
        statement.group_by(Match.tournament, Match.surface, Match.tour, Match.level)
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
    condition = (Match.tour == tour, Match.tournament == tournament_name)
    match_rows = db.session.scalars(
        select(Match).where(*condition)
        .options(joinedload(Match.winner), joinedload(Match.loser))
        .order_by(Match.week_start.desc(), Match.round.desc()).limit(12)
    ).all()
    finals = db.session.scalars(
        select(Match).where(*condition, Match.round == "F")
        .options(joinedload(Match.winner), joinedload(Match.loser))
        .order_by(Match.week_start.desc())
    ).all()
    total_matches, earliest, latest = db.session.execute(
        select(func.count(Match.id), func.min(Match.week_start), func.max(Match.week_start))
        .where(*condition)
    ).one()
    title_counts = {}
    for final in finals:
        title_counts.setdefault(final.winner_id, {"player": final.winner, "titles": 0})
        title_counts[final.winner_id]["titles"] += 1
    champions = sorted(
        title_counts.values(), key=lambda item: (-item["titles"], item["player"].name)
    )[:8]
    tournament_dates = db.session.scalars(
        select(Match.week_start).where(*condition).distinct()
    ).all()
    years = sorted({value.year for value in tournament_dates}, reverse=True)
    surfaces = db.session.scalars(
        select(Match.surface).where(*condition).distinct().order_by(Match.surface)
    ).all()
    levels = db.session.scalars(
        select(Match.level).where(*condition).distinct().order_by(Match.level)
    ).all()
    _, value_label, value_group = tournament_level(levels)
    subscriptions_ready = inspect(db.engine).has_table(TournamentSubscription.__tablename__)
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
    live_matches = [
        match for match in current_match_rows("live") + current_match_rows("upcoming")
        if match.tour == tour and match.tournament.casefold() == tournament_name.casefold()
    ]
    return render_template(
        "tournament.html", tournament=tournament_name, tour=tour,
        profile=tournament_profile(tournament_name), matches=match_rows,
        total_matches=total_matches, total_finals=len(finals), finals=finals[:12],
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
    matches = db.session.scalars(
        select(Match).where(
            Match.tour == tour, Match.tournament == tournament_name,
            func.extract("year", Match.week_start) == season,
        ).options(joinedload(Match.winner), joinedload(Match.loser))
        .order_by(Match.week_start, Match.round, Match.id)
    ).all()
    if not matches:
        abort(404)
    round_order = ("Q1", "Q2", "Q3", "R128", "R64", "R32", "R16", "QF", "SF", "F")
    rounds = {round_name: [match for match in matches if match.round == round_name]
              for round_name in round_order if any(match.round == round_name for match in matches)}
    final = next((match for match in matches if match.round == "F"), None)
    return render_template(
        "tournament_edition.html", tournament=tournament_name, tour=tour, season=season,
        matches=matches, rounds=rounds, final=final, surface=matches[0].surface,
        profile=tournament_profile(tournament_name), slug=slug, match_location=match_location,
    )


@site.post("/tournaments/<tour>/<slug>/subscribe")
@login_required
def toggle_tournament_subscription(tour, slug):
    if not inspect(db.engine).has_table(TournamentSubscription.__tablename__):
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
    players_found, matches_found, members = [], [], []
    if query:
        players_found = db.session.scalars(
            select(Player).where(Player.name.ilike(f"%{query}%")).order_by(Player.name).limit(15)
        ).all()
        winner, loser = aliased(Player), aliased(Player)
        matches_found = db.session.scalars(
            select(Match).join(winner, Match.winner).join(loser, Match.loser).where(or_(
                Match.tournament.ilike(f"%{query}%"), winner.name.ilike(f"%{query}%"),
                loser.name.ilike(f"%{query}%"),
            )).order_by(Match.week_start.desc()).limit(9)
        ).all()
        members = db.session.scalars(
            select(User).where(or_(User.username.ilike(f"%{query}%"), User.display_name.ilike(f"%{query}%")))
            .order_by(User.username).limit(15)
        ).all()
    return render_template(
        "search.html", query=query, players=players_found, matches=matches_found,
        members=members, match_location=match_location,
    )


@site.get("/news")
def news():
    source_keys = {source["key"] for source in NEWS_SOURCES}
    selected_source = request.args.get("source", "all").strip().lower()
    if selected_source not in source_keys:
        selected_source = "all"
    all_stories = [] if current_app.config["TESTING"] else curate_news_items(
        fetch_news_items(selected_source), minimum=8 if selected_source != "all" else 0,
    )
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    page_size = 30
    total_stories = len(all_stories)
    total_pages = max(1, (total_stories + page_size - 1) // page_size)
    page = min(page, total_pages)
    stories = all_stories[(page - 1) * page_size:page * page_size]
    featured_story = stories[0] if page == 1 and stories else None
    featured_article = None
    if featured_story is not None:
        featured_article = fetch_news_article(
            featured_story["source_key"], featured_story["id"], featured_story["url"]
        )
    feed_stories = stories[1:] if featured_story is not None else stories
    return render_template(
        "news.html", sources=NEWS_SOURCES, stories=stories, feed_stories=feed_stories,
        featured_story=featured_story, featured_article=featured_article, selected_source=selected_source,
        total_stories=total_stories, page=page, total_pages=total_pages,
    )


@site.get("/api/news-status")
def news_status():
    source_keys = {source["key"] for source in NEWS_SOURCES}
    selected_source = request.args.get("source", "all").strip().lower()
    if selected_source not in source_keys:
        selected_source = "all"
    stories = [] if current_app.config["TESTING"] else curate_news_items(
        fetch_news_items(selected_source), minimum=8 if selected_source != "all" else 0,
    )
    response = jsonify({
        "first_id": stories[0]["id"] if stories else None,
        "total": len(stories),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@site.get("/news/<source_key>/<story_id>")
def news_story(source_key, story_id):
    if source_key not in {source["key"] for source in NEWS_SOURCES}:
        abort(404)
    story = next((item for item in fetch_news_items(source_key) if item["id"] == story_id), None)
    if story is None:
        abort(404)
    article = fetch_news_article(source_key, story_id, story["url"])
    return render_template("news_story.html", story=story, article=article)


@site.get("/news/<source_key>/<story_id>/image")
def news_image(source_key, story_id):
    source = next((item for item in NEWS_SOURCES if item["key"] == source_key), None)
    if source is None:
        abort(404)
    story = next((item for item in fetch_news_items(source_key) if item["id"] == story_id), None)
    if story is None:
        abort(404)
    article = fetch_news_article(source_key, story_id, story["url"])
    image_url = article.get("image", "") if article else ""
    hostname = (urlsplit(image_url).hostname or "").lower()
    allowed_domain = source["image_domain"]
    if not image_url.startswith("https://") or not (
        hostname == allowed_domain or hostname.endswith(f".{allowed_domain}")
    ):
        abort(404)
    try:
        upstream = requests.get(
            image_url, headers={"Accept": "image/avif,image/webp,image/png,image/jpeg", "User-Agent": "Tennisd/1.0"},
            timeout=8,
        )
        upstream.raise_for_status()
    except requests.RequestException:
        abort(404)
    content_type = upstream.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if content_type not in {"image/avif", "image/jpeg", "image/png", "image/webp"} or len(upstream.content) > 5_000_000:
        abort(404)
    response = Response(upstream.content, mimetype=content_type)
    response.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=604800"
    response.headers["Vercel-CDN-Cache-Control"] = "max-age=86400, stale-while-revalidate=604800"
    return response


@site.get("/notifications")
@login_required
def notifications():
    requests_in = db.session.scalars(
        select(Friendship).where(Friendship.addressee_id == current_user.id, Friendship.status == "pending")
        .options(joinedload(Friendship.requester)).order_by(Friendship.created_at.desc())
    ).all()
    comments = db.session.scalars(
        select(Comment).join(Review).where(Review.user_id == current_user.id, Comment.user_id != current_user.id)
        .options(joinedload(Comment.user), joinedload(Comment.review).joinedload(Review.match))
        .order_by(Comment.created_at.desc()).limit(30)
    ).all()
    return render_template("notifications.html", requests_in=requests_in, comments=comments)


@site.post("/u/<username>/friend")
@login_required
def request_friend(username):
    other = db.session.scalar(select(User).where(User.username == username.lower()))
    if other is None:
        abort(404)
    if other.id == current_user.id:
        abort(400)
    existing = db.session.scalar(select(Friendship).where(or_(
        (Friendship.requester_id == current_user.id) & (Friendship.addressee_id == other.id),
        (Friendship.requester_id == other.id) & (Friendship.addressee_id == current_user.id),
    )))
    if existing is None:
        db.session.add(Friendship(requester_id=current_user.id, addressee_id=other.id))
        db.session.commit()
        flash("Friend request sent.", "success")
    return redirect(url_for("site.profile", username=other.username))


@site.post("/friend-requests/<int:request_id>/<action>")
@login_required
def answer_friend_request(request_id, action):
    item = db.get_or_404(Friendship, request_id)
    if item.addressee_id != current_user.id or item.status != "pending" or action not in {"accept", "decline"}:
        abort(403)
    if action == "accept":
        item.status = "accepted"
        flash("You are now friends.", "success")
    else:
        db.session.delete(item)
    db.session.commit()
    return redirect(url_for("site.notifications"))


@site.get("/players/<player_id>")
def player_detail(player_id):
    player = db.get_or_404(Player, player_id)
    update_prize_money(player)
    stats = player_statistics(player)
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
        match_location=match_location,
    )


@site.post("/players/<player_id>/follow")
@login_required
def follow_player(player_id):
    db.get_or_404(Player, player_id)
    follow = db.session.scalar(
        select(FollowedPlayer).where(
            FollowedPlayer.user_id == current_user.id,
            FollowedPlayer.player_id == player_id,
        )
    )
    if follow:
        db.session.delete(follow)
    else:
        db.session.add(FollowedPlayer(user_id=current_user.id, player_id=player_id))
    db.session.commit()
    return redirect(url_for("site.player_detail", player_id=player_id))


@site.route("/register", methods=["GET", "POST"])
def register():
    if not current_app.config["REGISTRATION_ENABLED"]:
        abort(503)
    if current_user.is_authenticated:
        return redirect(url_for("site.my_profile"))
    if request.method == "POST":
        limit_action("register-ip", client_ip(), 5, 3600)
        username = request.form.get("username", "").strip().lower()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not re.fullmatch(r"[a-z0-9_]{3,24}", username):
            flash("Username: 3–24 lowercase letters, numbers or underscores.", "error")
        elif not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 255:
            flash("Enter a valid email address.", "error")
        elif len(password) < 10 or len(password) > 128:
            flash("Use a password with 10–128 characters.", "error")
        elif db.session.scalar(select(User.id).where(or_(User.username == username, User.email == email))):
            flash("That username or email is already in use.", "error")
        else:
            user = User(username=username, email=email, display_name=username)
            user.set_password(password)
            user.auth_state = AuthState(
                email_verified_at=None if current_app.config["REQUIRE_EMAIL_VERIFICATION"] else utcnow()
            )
            db.session.add(user)
            db.session.commit()
            if current_app.config["REQUIRE_EMAIL_VERIFICATION"]:
                try:
                    send_account_email(user, "verify")
                    session["verification_email"] = user.email
                    flash("We sent a six-digit verification code to your email.", "success")
                except requests.RequestException:
                    current_app.logger.exception("Verification email delivery failed")
                    flash("We could not send your email. Use the resend link shortly.", "error")
                return redirect(url_for("site.check_email"))
            sign_in_user(user)
            flash("Welcome to Tennisd. Your diary is ready.", "success")
            return redirect(url_for("site.my_profile"))
    return render_template("auth.html", mode="register")


@site.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("site.my_profile"))
    if request.method == "POST":
        identity = request.form.get("identity", "").strip().lower()
        password = request.form.get("password", "")
        limit_action("login-ip", client_ip(), 20, 900)
        limit_action("login-identity", identity[:255], 8, 900)
        user = db.session.scalar(
            select(User).where(or_(User.username == identity, User.email == identity))
        )
        if user and len(password) <= 128 and user.check_password(password):
            if current_app.config["REQUIRE_EMAIL_VERIFICATION"] and (
                user.auth_state is None or user.auth_state.email_verified_at is None
            ):
                session["verification_email"] = user.email
                flash("Verify your email before logging in.", "error")
                return redirect(url_for("site.check_email"))
            if not user.password_hash.startswith("$argon2id$"):
                user.set_password(password)
                db.session.commit()
            sign_in_user(user)
            return redirect(safe_next("site.my_profile"))
        flash("Incorrect username, email or password.", "error")
    return render_template("auth.html", mode="login")


@site.route("/check-email", methods=["GET", "POST"])
def check_email():
    email = session.get("verification_email", "")
    if request.method == "POST":
        limit_action("verify-code-ip", client_ip(), 20, 900)
        email = request.form.get("email", "").strip().lower()[:255]
        code = request.form.get("code", "").strip()
        limit_action("verify-code-email", email, 10, 900)
        user = db.session.scalar(select(User).where(User.email == email))
        saved = valid_verification_code(user, code)
        if saved is None or user.auth_state is None or user.auth_state.email_verified_at is not None:
            flash("The code is incorrect or has expired.", "error")
        else:
            user.auth_state.email_verified_at = utcnow()
            db.session.delete(saved)
            db.session.commit()
            session.pop("verification_email", None)
            flash("Email verified. You can log in now.", "success")
            return redirect(url_for("site.login"))
    return render_template("auth_action.html", mode="check", verification_email=email)


@site.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    if not (current_app.config["RESEND_API_KEY"] or current_app.config.get("MAIL_DELIVERY")):
        abort(503)
    if request.method == "POST":
        limit_action("resend-ip", client_ip(), 5, 3600)
        email = request.form.get("email", "").strip().lower()[:255]
        limit_action("resend-email", email, 3, 3600)
        user = db.session.scalar(select(User).where(User.email == email))
        if user and (not user.auth_state or not user.auth_state.email_verified_at):
            try:
                send_account_email(user, "verify")
                session["verification_email"] = user.email
            except requests.RequestException:
                current_app.logger.exception("Verification email delivery failed")
        flash("If this address needs verification, a new code has been sent.", "success")
        return redirect(url_for("site.check_email"))
    return render_template("auth_action.html", mode="resend")


@site.route("/verify-email/<token>", methods=["GET", "POST"])
def verify_email(token):
    saved = valid_token(token, "verify")
    if saved is None:
        flash("This verification link has expired. Request a new one.", "error")
        return redirect(url_for("site.resend_verification"))
    if request.method == "POST":
        user = saved.user
        if user.auth_state is None:
            user.auth_state = AuthState(session_version=0)
        user.auth_state.email_verified_at = utcnow()
        db.session.delete(saved)
        db.session.commit()
        flash("Email verified. You can log in now.", "success")
        return redirect(url_for("site.login"))
    return render_template("auth_action.html", mode="verify", token=token)


@site.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if not (current_app.config["RESEND_API_KEY"] or current_app.config.get("MAIL_DELIVERY")):
        abort(503)
    if request.method == "POST":
        limit_action("reset-ip", client_ip(), 5, 3600)
        email = request.form.get("email", "").strip().lower()[:255]
        user = db.session.scalar(select(User).where(User.email == email))
        if user:
            try:
                send_account_email(user, "reset")
            except requests.RequestException:
                current_app.logger.exception("Password reset email delivery failed")
        flash("If an account uses this address, a reset link has been sent.", "success")
        return redirect(url_for("site.login"))
    return render_template("auth_action.html", mode="forgot")


@site.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    saved = valid_token(token, "reset")
    if saved is None:
        flash("This reset link has expired. Request a new one.", "error")
        return redirect(url_for("site.forgot_password"))
    if request.method == "POST":
        limit_action("reset-submit-ip", client_ip(), 10, 3600)
        password = request.form.get("password", "")
        if not 10 <= len(password) <= 128:
            flash("Use a password with 10–128 characters.", "error")
        else:
            user = saved.user
            user.set_password(password)
            if user.auth_state is None:
                user.auth_state = AuthState(session_version=0)
            user.auth_state.session_version += 1
            db.session.execute(delete(AuthToken).where(AuthToken.user_id == user.id))
            db.session.commit()
            session.clear()
            flash("Password changed. Log in with your new password.", "success")
            return redirect(url_for("site.login"))
    return render_template("auth_action.html", mode="reset", token=token)


@site.post("/logout")
@login_required
def logout():
    session.clear()
    logout_user()
    return redirect(url_for("site.home"))


@site.get("/me")
@login_required
def my_profile():
    return redirect(url_for("site.profile", username=current_user.username))


@site.get("/u/<username>")
def profile(username):
    user = db.session.scalar(select(User).where(User.username == username.lower()))
    if user is None:
        abort(404)
    own = current_user.is_authenticated and current_user.id == user.id
    available_tabs = {"profile", "diary", "likes"}
    if own:
        available_tabs.update({"watchlist", "friends"})
    active_tab = request.args.get("tab", "profile").lower()
    if active_tab not in available_tabs:
        active_tab = "profile"
    statement = select(Review).where(Review.user_id == user.id)
    if not own:
        statement = statement.where(Review.is_public.is_(True))
    reviews = db.session.scalars(
        statement.options(joinedload(Review.match).joinedload(Match.winner),
                          joinedload(Review.match).joinedload(Match.loser))
        .order_by(Review.watched_on.desc(), Review.id.desc())
    ).all()
    followed = []
    tournament_subscriptions = []
    watchlist = []
    friends = []
    friend_state = None
    if own:
        followed = db.session.scalars(
            select(FollowedPlayer).where(FollowedPlayer.user_id == user.id)
            .options(joinedload(FollowedPlayer.player)).limit(12)
        ).all()
        watchlist = db.session.scalars(
            select(WatchlistItem).where(WatchlistItem.user_id == user.id)
            .options(joinedload(WatchlistItem.match))
            .order_by(WatchlistItem.added_at.desc())
        ).all()
        if inspect(db.engine).has_table(TournamentSubscription.__tablename__):
            tournament_subscriptions = db.session.scalars(
                select(TournamentSubscription).where(TournamentSubscription.user_id == user.id)
                .order_by(TournamentSubscription.created_at.desc())
            ).all()
        connections = db.session.scalars(select(Friendship).where(
            or_(Friendship.requester_id == user.id, Friendship.addressee_id == user.id),
            Friendship.status == "accepted",
        ).options(joinedload(Friendship.requester), joinedload(Friendship.addressee))).all()
        friends = [item.addressee if item.requester_id == user.id else item.requester for item in connections]
    elif current_user.is_authenticated:
        friend_state = db.session.scalar(select(Friendship.status).where(or_(
            (Friendship.requester_id == current_user.id) & (Friendship.addressee_id == user.id),
            (Friendship.requester_id == user.id) & (Friendship.addressee_id == current_user.id),
        )))
    favorites = [review for review in reviews if review.is_favorite]
    return render_template(
        "profile.html", user=user, reviews=reviews,
        stats=diary_statistics(reviews), own=own, followed=followed, watchlist=watchlist,
        favorites=favorites, active_tab=active_tab, friends=friends, friend_state=friend_state,
        tournament_subscriptions=tournament_subscriptions, tournament_slug=tournament_slug,
    )


@site.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        bio = request.form.get("bio", "").strip()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        avatar_upload = request.files.get("avatar")
        avatar_data = None
        avatar_error = None
        if avatar_upload and avatar_upload.filename:
            limit_action("profile-image", str(current_user.id), 10, 3600)
            try:
                avatar_data = prepare_profile_image(avatar_upload)
            except ValueError as error:
                avatar_error = str(error)
        if new_password:
            limit_action("password-change", str(current_user.id), 5, 3600)
        if avatar_error:
            flash(avatar_error, "error")
        elif not 1 <= len(display_name) <= 60 or len(bio) > 280:
            flash("Display name or bio is too long.", "error")
        elif new_password and (
            not current_user.check_password(current_password) or not 10 <= len(new_password) <= 128
        ):
            flash("Check your current password; the new one needs 10–128 characters.", "error")
        else:
            current_user.display_name = display_name
            current_user.bio = bio
            if avatar_data is not None:
                if current_user.profile_image is None:
                    current_user.profile_image = ProfileImage(mime_type="image/webp", image_data=avatar_data)
                else:
                    current_user.profile_image.mime_type = "image/webp"
                    current_user.profile_image.image_data = avatar_data
                    current_user.profile_image.updated_at = utcnow()
            elif request.form.get("remove_avatar") == "on":
                current_user.profile_image = None
            if new_password:
                current_user.set_password(new_password)
                if current_user.auth_state is None:
                    current_user.auth_state = AuthState(session_version=0)
                current_user.auth_state.session_version += 1
            db.session.commit()
            if new_password:
                sign_in_user(current_user)
            flash("Settings saved.", "success")
            return redirect(url_for("site.settings"))
    return render_template("settings.html")


@site.get("/settings/export")
@login_required
def export_diary():
    reviews = db.session.scalars(
        select(Review).where(Review.user_id == current_user.id)
        .options(joinedload(Review.match)).order_by(Review.watched_on)
    ).all()
    follows = db.session.scalars(
        select(FollowedPlayer).where(FollowedPlayer.user_id == current_user.id)
        .options(joinedload(FollowedPlayer.player))
    ).all()
    watchlist = db.session.scalars(
        select(WatchlistItem).where(WatchlistItem.user_id == current_user.id)
    ).all()
    tournament_subscriptions = []
    if inspect(db.engine).has_table(TournamentSubscription.__tablename__):
        tournament_subscriptions = db.session.scalars(
            select(TournamentSubscription).where(TournamentSubscription.user_id == current_user.id)
            .order_by(TournamentSubscription.created_at)
        ).all()
    comments = db.session.scalars(
        select(Comment).where(Comment.user_id == current_user.id)
        .options(joinedload(Comment.review))
        .order_by(Comment.created_at)
    ).all()
    payload = {
        "username": current_user.username,
        "email": current_user.email,
        "display_name": current_user.display_name,
        "bio": current_user.bio,
        "has_profile_photo": current_user.profile_image is not None,
        "joined_at": current_user.created_at.isoformat(),
        "diary": [
            {
                "match_id": review.match_id,
                "match": f"{review.match.winner.name} vs {review.match.loser.name}",
                "tournament": review.match.tournament,
                "watched_on": review.watched_on.isoformat(),
                "rating_out_of_ten": review.rating_half,
                "review": review.body,
                "favorite": review.is_favorite,
                "public": review.is_public,
                "spoilers": review.has_spoilers,
            }
            for review in reviews
        ],
        "followed_players": [item.player.name for item in follows],
        "followed_tournaments": [
            {"tour": item.tour, "tournament": item.tournament}
            for item in tournament_subscriptions
        ],
        "watchlist_match_ids": [item.match_id for item in watchlist],
        "comments": [
            {
                "match_id": comment.review.match_id,
                "review_id": comment.review_id,
                "body": comment.body,
                "created_at": comment.created_at.isoformat(),
            }
            for comment in comments
        ],
    }
    response = jsonify(payload)
    response.headers["Content-Disposition"] = f'attachment; filename="tennisd-{current_user.username}.json"'
    response.cache_control.no_store = True
    return response


@site.post("/settings/delete-account")
@login_required
def delete_account():
    limit_action("account-delete", str(current_user.id), 5, 3600)
    if (
        request.form.get("username", "").strip().lower() != current_user.username
        or not current_user.check_password(request.form.get("password", ""))
    ):
        flash("Username or password was incorrect. Your account was not deleted.", "error")
        return redirect(url_for("site.settings"))
    user = db.session.get(User, current_user.id)
    db.session.execute(delete(Friendship).where(or_(
        Friendship.requester_id == user.id, Friendship.addressee_id == user.id,
    )))
    db.session.execute(delete(Comment).where(Comment.user_id == user.id))
    db.session.execute(delete(FollowedPlayer).where(FollowedPlayer.user_id == user.id))
    if inspect(db.engine).has_table(TournamentSubscription.__tablename__):
        db.session.execute(delete(TournamentSubscription).where(TournamentSubscription.user_id == user.id))
    db.session.execute(delete(WatchlistItem).where(WatchlistItem.user_id == user.id))
    db.session.delete(user)
    db.session.commit()
    session.clear()
    logout_user()
    flash("Your account and diary have been deleted.", "success")
    return redirect(url_for("site.home"))


@site.post("/matches/<path:match_id>/log")
@login_required
def log_match(match_id):
    match = db.get_or_404(Match, match_id)
    try:
        watched_on = date.fromisoformat(request.form.get("watched_on", ""))
        if watched_on > date.today() or watched_on < match.week_start:
            raise ValueError
        rating_text = request.form.get("rating", "")
        rating = int(rating_text) if rating_text else None
        if rating is not None and not 1 <= rating <= 10:
            raise ValueError
    except ValueError:
        flash("Choose a valid viewing date and rating.", "error")
        return redirect(url_for("site.match_detail", match_id=match_id))

    body = request.form.get("body", "").strip()
    if len(body) > 3000:
        flash("Keep your review under 3,000 characters.", "error")
        return redirect(url_for("site.match_detail", match_id=match_id))
    review = db.session.scalar(
        select(Review).where(Review.user_id == current_user.id, Review.match_id == match_id)
    )
    if review is None:
        review = Review(user_id=current_user.id, match_id=match_id)
        db.session.add(review)
    review.watched_on = watched_on
    review.rating_half = rating
    review.body = body
    review.is_favorite = request.form.get("favorite") == "on"
    review.is_public = request.form.get("public") == "on"
    review.has_spoilers = request.form.get("spoilers") == "on"
    watchlist_item = db.session.scalar(
        select(WatchlistItem).where(
            WatchlistItem.user_id == current_user.id, WatchlistItem.match_id == match_id
        )
    )
    if watchlist_item:
        db.session.delete(watchlist_item)
    db.session.commit()
    flash("Your match diary entry was saved.", "success")
    return redirect(url_for("site.match_detail", match_id=match_id))


@site.post("/reviews/<int:review_id>/delete")
@login_required
def delete_review(review_id):
    review = db.get_or_404(Review, review_id)
    if review.user_id != current_user.id:
        abort(403)
    match_id = review.match_id
    db.session.delete(review)
    db.session.commit()
    flash("Diary entry deleted.", "success")
    return redirect(url_for("site.match_detail", match_id=match_id))


@site.post("/reviews/<int:review_id>/comments")
@login_required
def add_comment(review_id):
    review = db.get_or_404(Review, review_id)
    if not review.is_public:
        abort(404)
    body = request.form.get("body", "").strip()
    if not 1 <= len(body) <= 500:
        flash("Comments must be 1–500 characters.", "error")
    else:
        recent = db.session.scalar(
            select(Comment).where(
                Comment.user_id == current_user.id,
                Comment.created_at > datetime.now(timezone.utc) - timedelta(seconds=15),
            ).limit(1)
        )
        if recent:
            flash("Please wait a moment before commenting again.", "error")
        else:
            db.session.add(Comment(review_id=review.id, user_id=current_user.id, body=body))
            db.session.commit()
            flash("Comment added.", "success")
    return redirect(url_for("site.match_detail", match_id=review.match_id) + f"#review-{review.id}")


@site.post("/comments/<int:comment_id>/delete")
@login_required
def delete_comment(comment_id):
    comment = db.get_or_404(Comment, comment_id)
    if comment.user_id != current_user.id and comment.review.user_id != current_user.id:
        abort(403)
    match_id = comment.review.match_id
    review_id = comment.review_id
    db.session.delete(comment)
    db.session.commit()
    return redirect(url_for("site.match_detail", match_id=match_id) + f"#review-{review_id}")


@site.post("/reports")
@login_required
def report_content():
    limit_action("report-user", str(current_user.id), 10, 86400)
    target = request.form.get("target", "")
    reason = request.form.get("reason", "")
    if reason not in {"spam", "harassment", "hate", "other"}:
        abort(400)
    if target == "review":
        review = db.get_or_404(Review, request.form.get("target_id", type=int))
        if not review.is_public:
            abort(404)
        match_id = review.match_id
        condition = (Report.reporter_id == current_user.id, Report.review_id == review.id)
        report = Report(reporter_id=current_user.id, review_id=review.id, reason=reason)
    elif target == "comment":
        comment = db.get_or_404(Comment, request.form.get("target_id", type=int))
        if not comment.review.is_public:
            abort(404)
        match_id = comment.review.match_id
        condition = (Report.reporter_id == current_user.id, Report.comment_id == comment.id)
        report = Report(reporter_id=current_user.id, comment_id=comment.id, reason=reason)
    else:
        abort(400)
    if db.session.scalar(select(Report.id).where(*condition, Report.status == "open")) is None:
        db.session.add(report)
        db.session.commit()
    flash("Thank you. This report is queued for review.", "success")
    return redirect(url_for("site.match_detail", match_id=match_id))


def require_moderator():
    state = current_user.auth_state if current_user.is_authenticated else None
    if (
        not current_user.is_authenticated
        or current_user.email != current_app.config["ADMIN_EMAIL"]
        or not state or not state.email_verified_at
    ):
        abort(403)


def feedback_tables_ready():
    inspector = inspect(db.engine)
    return all(inspector.has_table(name) for name in ("feedback_submission", "poll", "poll_option", "poll_vote"))


def feedback_visitor_key():
    token = session.get("feedback_visitor")
    if not token:
        token = secrets.token_urlsafe(24)
        session["feedback_visitor"] = token
    identity = f"user:{current_user.id}" if current_user.is_authenticated else f"guest:{token}"
    return hmac.new(current_app.secret_key.encode(), identity.encode(), "sha256").hexdigest()


@site.route("/feedback", methods=["GET", "POST"])
def feedback():
    ready = feedback_tables_ready()
    if request.method == "POST":
        if not ready:
            abort(503)
        category = request.form.get("category", "").strip().lower()
        message = request.form.get("message", "").strip()
        raw_rating = request.form.get("rating", "").strip()
        page_path = request.form.get("page_path", "").strip()
        rating = int(raw_rating) if raw_rating.isdigit() else None
        if category not in {"idea", "bug", "content", "other"}:
            flash("Choose a feedback category.", "error")
        elif rating is not None and rating not in range(1, 6):
            flash("Choose a rating from 1 to 5.", "error")
        elif not 10 <= len(message) <= 2000:
            flash("Write between 10 and 2,000 characters.", "error")
        else:
            limit_action("feedback", current_user.id if current_user.is_authenticated else client_ip(), 5, 3600)
            if (
                not page_path.startswith("/") or page_path.startswith("//")
                or "\\" in page_path or len(page_path) > 300
            ):
                page_path = None
            db.session.add(FeedbackSubmission(
                user_id=current_user.id if current_user.is_authenticated else None,
                category=category,
                rating=rating,
                message=message,
                page_path=page_path,
            ))
            db.session.commit()
            flash("Thank you — your feedback is now in the Tennisd roadmap inbox.", "success")
            return redirect(url_for("site.feedback"))

    poll = None
    selected_option_id = None
    if ready:
        now = utcnow()
        poll = db.session.scalar(
            select(Poll).where(
                Poll.is_active.is_(True), Poll.starts_at <= now,
                or_(Poll.ends_at.is_(None), Poll.ends_at > now),
            ).order_by(Poll.created_at.desc())
        )
        if poll:
            vote = db.session.scalar(select(PollVote).where(
                PollVote.poll_id == poll.id,
                PollVote.visitor_key == feedback_visitor_key(),
            ))
            selected_option_id = vote.option_id if vote else None
    return render_template("feedback.html", ready=ready, poll=poll, selected_option_id=selected_option_id)


@site.post("/feedback/polls/<int:poll_id>/vote")
def vote_poll(poll_id):
    if not feedback_tables_ready():
        abort(503)
    now = utcnow()
    poll = db.session.scalar(select(Poll).where(
        Poll.id == poll_id, Poll.is_active.is_(True), Poll.starts_at <= now,
        or_(Poll.ends_at.is_(None), Poll.ends_at > now),
    ))
    if not poll:
        abort(404)
    raw_option = request.form.get("option", "")
    option = db.session.get(PollOption, int(raw_option)) if raw_option.isdigit() else None
    if not option or option.poll_id != poll.id:
        flash("Choose one answer.", "error")
        return redirect(url_for("site.feedback"))
    visitor_key = feedback_visitor_key()
    limit_action("poll-vote", visitor_key, 10, 3600)
    vote = db.session.scalar(select(PollVote).where(
        PollVote.poll_id == poll.id, PollVote.visitor_key == visitor_key,
    ))
    if vote:
        vote.option_id = option.id
    else:
        db.session.add(PollVote(
            poll_id=poll.id, option_id=option.id,
            user_id=current_user.id if current_user.is_authenticated else None,
            visitor_key=visitor_key,
        ))
    db.session.commit()
    flash("Vote saved. Thanks for helping shape Tennisd.", "success")
    return redirect(url_for("site.feedback"))


@site.get("/moderation")
@login_required
def moderation():
    require_moderator()
    reports = db.session.scalars(
        select(Report).where(Report.status == "open")
        .order_by(Report.created_at.asc()).limit(100)
    ).all()
    feedback_items = []
    feedback_summary = {"total": 0, "average": None, "categories": []}
    polls = []
    if feedback_tables_ready():
        feedback_items = db.session.scalars(
            select(FeedbackSubmission).order_by(FeedbackSubmission.created_at.desc()).limit(100)
        ).all()
        total, average = db.session.execute(
            select(func.count(FeedbackSubmission.id), func.avg(FeedbackSubmission.rating))
        ).one()
        categories = db.session.execute(
            select(FeedbackSubmission.category, func.count(FeedbackSubmission.id))
            .group_by(FeedbackSubmission.category).order_by(func.count(FeedbackSubmission.id).desc())
        ).all()
        feedback_summary = {"total": total, "average": average, "categories": categories}
        polls = db.session.scalars(select(Poll).order_by(Poll.created_at.desc())).all()
    return render_template(
        "moderation.html", reports=reports, feedback_items=feedback_items,
        feedback_summary=feedback_summary, polls=polls,
    )


@site.post("/moderation/feedback/<int:feedback_id>/<status>")
@login_required
def moderate_feedback(feedback_id, status):
    require_moderator()
    if status not in {"new", "reviewed", "planned", "closed"}:
        abort(400)
    item = db.get_or_404(FeedbackSubmission, feedback_id)
    item.status = status
    db.session.commit()
    return redirect(url_for("site.moderation") + "#feedback")


@site.post("/moderation/reports/<int:report_id>/<action>")
@login_required
def moderate_report(report_id, action):
    require_moderator()
    report = db.get_or_404(Report, report_id)
    if action == "dismiss":
        report.status = "dismissed"
    elif action == "remove":
        target = report.review or report.comment
        db.session.delete(target)
    else:
        abort(400)
    db.session.commit()
    return redirect(url_for("site.moderation"))


@site.get("/about")
def about():
    return render_template("about.html")


@site.get("/privacy")
def privacy():
    return render_template("privacy.html")
