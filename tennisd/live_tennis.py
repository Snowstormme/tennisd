"""Import the free current top-tier slate from Live Tennis API."""

import os
import re
import hashlib
import unicodedata
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from . import db
from .models import LiveMatch, Match, Player, PlayerExternalId


API_ROOT = "https://api.livetennisapi.com/api/public/v1"
GRAND_SLAMS = {
    "australian open": "Australian Open",
    "french open": "Roland Garros",
    "roland garros": "Roland Garros",
    "wimbledon": "Wimbledon",
    "us open": "US Open",
    "u.s. open": "US Open",
}
FEATURED_TIERS = (
    "grand_slam",
    "atp_1000",
    "atp_500",
    "wta_1000",
    "wta_500",
    "atp_250", "wta_250", "wta_125",
    "challenger_175", "challenger_125", "challenger_100", "challenger_75", "challenger_50",
    "itf_m15", "itf_m25", "itf_w15", "itf_w25", "itf_w35", "itf_w40",
    "itf_w50", "itf_w60", "itf_w75", "itf_w80", "itf_w100",
)


def normalized_player_name(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(character for character in value if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def live_player_id(tour, provider_id, name):
    identity = provider_id or normalized_player_name(name)
    digest = hashlib.sha256(f"{tour}:{identity}".encode()).hexdigest()[:16]
    return f"lt-{digest}"


def ensure_live_player_profiles(matches):
    """Attach singles-feed identities to permanent Tennisd player profiles."""
    participants = []
    for match in matches:
        if match.is_doubles:
            continue
        participants.extend((
            (match.tour, match.player1_name, match.player1_provider_id),
            (match.tour, match.player2_name, match.player2_provider_id),
        ))
    if not participants:
        return 0

    provider_ids = {provider_id for _, _, provider_id in participants if provider_id}
    external_rows = db.session.scalars(
        select(PlayerExternalId)
        .where(
            PlayerExternalId.provider == "livetennisapi",
            PlayerExternalId.external_id.in_(provider_ids),
        )
        .options(joinedload(PlayerExternalId.player))
    ).all() if provider_ids else []
    by_external = {row.external_id: row.player for row in external_rows}

    lowered_names = {name.casefold() for _, name, _ in participants if name}
    named_players = db.session.scalars(
        select(Player).where(func.lower(Player.name).in_(lowered_names))
    ).all() if lowered_names else []
    by_name = {(player.tour, normalized_player_name(player.name)): player for player in named_players}

    created = 0
    mapped_ids = set(by_external)
    for tour, name, provider_id in participants:
        player = by_external.get(provider_id) if provider_id else None
        if player is None:
            key = (tour, normalized_player_name(name))
            player = by_name.get(key)
            if player is None:
                player = Player(id=live_player_id(tour, provider_id, name), tour=tour, name=name)
                db.session.add(player)
                by_name[key] = player
                created += 1
        if provider_id and provider_id not in mapped_ids:
            db.session.add(PlayerExternalId(
                player=player, provider="livetennisapi", external_id=provider_id,
            ))
            by_external[provider_id] = player
            mapped_ids.add(provider_id)
    return created


def parse_instant(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def slam_name(raw_name):
    lowered = (raw_name or "").lower()
    for marker, canonical in GRAND_SLAMS.items():
        if marker in lowered:
            return canonical
    return None


def display_score(score):
    if not isinstance(score, dict):
        return ""
    games = score.get("games")
    if not isinstance(games, list) or len(games) != 2:
        return ""
    first, second = games
    if not isinstance(first, list) or not isinstance(second, list):
        return ""
    sets = []
    for left, right in zip(first, second):
        if isinstance(left, int) and isinstance(right, int):
            sets.append(f"{left}–{right}")
    points = score.get("points")
    if isinstance(points, list) and len(points) == 2 and any(str(point) not in ("0", "") for point in points):
        sets.append(f"({points[0]}–{points[1]})")
    return " ".join(sets)


def winner_from_score(score):
    sets = re.findall(r"(\d+)[–-](\d+)", score or "")
    def completed_winner(left, right):
        left, right = int(left), int(right)
        if (left >= 6 or right >= 6) and abs(left - right) >= 2:
            return 1 if left > right else 2
        if (left, right) in ((7, 5), (7, 6)):
            return 1
        if (right, left) in ((7, 5), (7, 6)):
            return 2
        return None
    completed = [completed_winner(left, right) for left, right in sets]
    won1 = completed.count(1)
    won2 = completed.count(2)
    return 1 if won1 >= 2 and won1 > won2 else 2 if won2 >= 2 and won2 > won1 else None


def score_is_aged(provider_updated_at, now, minutes=30):
    if not provider_updated_at:
        return False
    if provider_updated_at.tzinfo is None:
        provider_updated_at = provider_updated_at.replace(tzinfo=timezone.utc)
    return provider_updated_at <= now - timedelta(minutes=minutes)


def score_is_final(match):
    """Require enough completed sets and no in-game point score before archiving."""
    if not match.score or "(" in match.score:
        return False
    sets = re.findall(r"(\d+)[–-](\d+)", match.score)
    completed = []
    for left, right in sets:
        left, right = int(left), int(right)
        if (left >= 6 or right >= 6) and abs(left - right) >= 2:
            completed.append(1 if left > right else 2)
        elif (left, right) in ((7, 5), (7, 6)):
            completed.append(1)
        elif (right, left) in ((7, 5), (7, 6)):
            completed.append(2)
    required = 3 if match.tour == "ATP" and match.tier == "grand_slam" and match.draw == "singles" else 2
    return completed.count(1) >= required or completed.count(2) >= required


def score_from_winner_side(score, winner_side):
    if winner_side != 2:
        return score
    return re.sub(r"(\d+)[–-](\d+)", lambda item: f"{item.group(2)}–{item.group(1)}", score or "")


def archive_level(tier):
    tier = (tier or "").lower()
    if tier == "grand_slam":
        return "G"
    if tier == "atp_1000":
        return "M"
    if tier == "wta_1000":
        return "PM"
    if tier.startswith("atp_"):
        return "A"
    if tier.startswith("wta_"):
        return "P"
    return "I"


def permanent_profile(match, side):
    provider_id = match.player1_provider_id if side == 1 else match.player2_provider_id
    name = match.player1_name if side == 1 else match.player2_name
    if provider_id:
        profile = db.session.scalar(
            select(Player).join(PlayerExternalId).where(
                PlayerExternalId.provider == "livetennisapi",
                PlayerExternalId.external_id == provider_id,
            )
        )
        if profile:
            return profile
    return db.session.scalar(
        select(Player).where(Player.tour == match.tour, func.lower(Player.name) == name.casefold())
    )


def preserve_finished_match(live_match, now):
    """Copy a settled singles feed row into the permanent community catalog once."""
    if (
        live_match.is_doubles or live_match.winner_side not in (1, 2)
        or not live_match.starts_at or not score_is_final(live_match)
    ):
        return None
    provider_id = f"livetennisapi:{live_match.provider_id}"
    existing = db.session.scalar(select(Match).where(Match.provider_id == provider_id))
    if existing:
        return existing
    winner = permanent_profile(live_match, live_match.winner_side)
    loser = permanent_profile(live_match, 2 if live_match.winner_side == 1 else 1)
    if not winner or not loser:
        return None
    archived = Match(
        id=f"lt-{live_match.provider_id}", provider="livetennisapi", provider_id=provider_id,
        status="finished", tour=live_match.tour, tournament=live_match.tournament,
        level=archive_level(live_match.tier), surface=live_match.surface,
        week_start=live_match.starts_at.date(), scheduled_at=live_match.starts_at,
        completed_at=now, round=(live_match.round or "R?")[:4],
        winner=winner, loser=loser,
        score=score_from_winner_side(live_match.score, live_match.winner_side),
    )
    db.session.add(archived)
    return archived


def normalize_match(item, now=None):
    now = now or datetime.now(timezone.utc)
    tier = str(item.get("tier") or "").lower()
    tournament = slam_name(item.get("tournament")) or str(item.get("tournament") or "").strip()
    raw_tour = str(item.get("tour") or "").lower()
    tour = "WTA" if tier.startswith(("wta_", "itf_w")) else "ATP" if tier.startswith(("atp_", "challenger_", "itf_m")) else raw_tour.upper()
    if not tournament or len(tournament) > 160 or tier not in FEATURED_TIERS or tour not in ("ATP", "WTA"):
        return None
    draw = str(item.get("draw") or "singles").lower()
    if draw not in ("singles", "qualifying", "doubles"):
        return None
    status = item.get("status")
    if status not in ("live", "upcoming"):
        return None
    starts_at = parse_instant(item.get("scheduled_time"))
    if status == "upcoming" and (not starts_at or starts_at < now - timedelta(hours=6) or starts_at > now + timedelta(days=7)):
        return None
    players = item.get("players") or {}
    p1, p2 = players.get("p1") or {}, players.get("p2") or {}
    if not p1.get("name") or not p2.get("name"):
        return None
    surface = str(item.get("surface") or "hard").title()
    return {
        "provider_id": str(item["id"]),
        "status": status,
        "tour": tour,
        "tournament": tournament,
        "tournament_id": str(item.get("tournament_id") or "") or None,
        "surface": surface if surface in ("Hard", "Clay", "Grass") else "Hard",
        "round": item.get("round_code") or item.get("round") or None,
        "draw": draw,
        "is_doubles": item.get("is_doubles") is True or draw == "doubles",
        "tier": tier,
        "starts_at": starts_at,
        "player1_name": p1["name"],
        "player2_name": p2["name"],
        "player1_provider_id": str(p1.get("id") or "") or None,
        "player2_provider_id": str(p2.get("id") or "") or None,
        "score": display_score(item.get("score")),
        "server": (item.get("score") or {}).get("server"),
        "provider_updated_at": parse_instant((item.get("score") or {}).get("timestamp")),
        "synced_at": now,
    }


def fetch_matches(status, session=requests):
    key = os.environ.get("LIVETENNISAPI_KEY", "").strip()
    if not key:
        raise RuntimeError("LIVETENNISAPI_KEY is not configured.")
    matches = []
    offset = 0
    # Stay within the free 100 requests/day allowance at a 15-minute schedule:
    # 92 live runs use one page and four midnight upcoming runs use two pages.
    max_pages = 2 if status == "upcoming" else 1
    pages = 0
    while pages < max_pages:
        response = session.get(
            f"{API_ROOT}/matches",
            params={
                "status": status,
                "tier": ",".join(FEATURED_TIERS),
                "limit": 100,
                "offset": offset,
            },
            headers={"X-API-Key": key, "User-Agent": "Tennisd/1.0"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        page = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(page, list):
            break
        matches.extend(page)
        pages += 1
        if len(page) < 100:
            break
        offset += len(page)
    return matches


def sync_matches(status, session=requests, now=None):
    if status not in ("live", "upcoming"):
        raise ValueError("status must be live or upcoming")
    now = now or datetime.now(timezone.utc)
    rows = [normalize_match(item, now) for item in fetch_matches(status, session)]
    rows = [row for row in rows if row]
    current_ids = {row["provider_id"] for row in rows}
    for row in rows:
        match = db.session.get(LiveMatch, row["provider_id"])
        if match is None:
            match = LiveMatch(provider_id=row["provider_id"])
            db.session.add(match)
        for field, value in row.items():
            setattr(match, field, value)
    db.session.flush()
    recent_feed_matches = db.session.scalars(
        select(LiveMatch)
        .where(LiveMatch.is_doubles.is_(False))
        .order_by(LiveMatch.synced_at.desc())
        .limit(500)
    ).all()
    ensure_live_player_profiles(recent_feed_matches)
    stale_statuses = (status, "verifying") if status == "live" else (status,)
    stale = LiveMatch.query.filter(LiveMatch.status.in_(stale_statuses)).all()
    for match in stale:
        if match.provider_id not in current_ids:
            # Keep the same provider record permanently. A later reconciliation
            # can enrich its final outcome without losing its identity or score.
            winner = winner_from_score(match.score) if status == "live" else None
            score_aged = score_is_aged(match.provider_updated_at, now)
            verified = status == "live" and winner and score_is_final(match) and score_aged
            match.status = "finished" if verified else "verifying" if status == "live" else "cancelled"
            match.finished_at = now if verified else None
            match.outcome = "completed" if verified else "pending_verification" if status == "live" else "cancelled"
            match.winner_side = winner if verified else None
            match.synced_at = now
            if verified:
                preserve_finished_match(match, now)
    if status == "upcoming":
        LiveMatch.query.filter(LiveMatch.starts_at > now + timedelta(days=7)).delete(synchronize_session=False)
    db.session.commit()
    return len(rows)
