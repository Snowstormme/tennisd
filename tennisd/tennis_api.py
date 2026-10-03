"""Quota-aware historical result import from Tennis API on RapidAPI."""

import hashlib
import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

import requests
from sqlalchemy import func, select

from . import db
from .models import (
    IngestionCursor,
    IngestionRun,
    Match,
    MatchParticipant,
    Player,
    PlayerExternalId,
    Tournament,
    TournamentEdition,
)
from .tournament_catalog import tournament_profile, tournament_slug


API_HOST = "tennis-api-atp-wta-itf.p.rapidapi.com"
API_ROOT = f"https://{API_HOST}/tennis/v2"
PROVIDER = "tennis_api"
BACKFILL_FLOOR = date(2010, 1, 1)
BACKFILL_DAYS = 7
PAGE_SIZE = 500
ROUND_NAMES = {
    0: "Q1", 1: "Q2", 2: "Q3", 3: "Q4", 4: "R128", 5: "R64",
    6: "R32", 7: "R16", 8: "R16", 9: "QF", 10: "SF", 11: "BR", 12: "F",
}
COURTS = {1: "Hard", 2: "Clay", 3: "Hard", 4: "Carpet", 5: "Grass"}


class RequestBudgetExhausted(RuntimeError):
    pass


def normalized_name(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(character for character in value if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def parse_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def as_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def surface_from(tournament):
    court = tournament.get("court") or {}
    raw = court.get("name") if isinstance(court, dict) else court
    raw = str(raw or "").casefold()
    if "grass" in raw:
        return "Grass"
    if "clay" in raw:
        return "Clay"
    if "carpet" in raw:
        return "Carpet"
    return COURTS.get(as_int(tournament.get("courtId")), "Hard")


def level_from(tour, tournament):
    rank_id = as_int(tournament.get("rankId"))
    if rank_id == 4:
        return "G"
    if rank_id == 3:
        return "M" if tour == "ATP" else "PM"
    if rank_id == 2:
        return "A" if tour == "ATP" else "P"
    if rank_id == 7:
        return "F"
    if rank_id == 5:
        return "D"
    return "I"


def player_id(tour, external_id, name):
    identity = str(external_id or normalized_name(name))
    digest = hashlib.sha256(f"{tour}:{identity}".encode()).hexdigest()[:16]
    return f"ta-{digest}"


def ensure_player(tour, raw):
    external_id = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    player = None
    if external_id:
        player = db.session.scalar(
            select(Player).join(PlayerExternalId).where(
                PlayerExternalId.provider == PROVIDER,
                PlayerExternalId.external_id == external_id,
            )
        )
    if player is None:
        candidates = db.session.scalars(
            select(Player).where(Player.tour == tour, func.lower(Player.name) == name.casefold())
        ).all()
        player = next((item for item in candidates if normalized_name(item.name) == normalized_name(name)), None)
    if player is None:
        player = Player(id=player_id(tour, external_id, name), tour=tour, name=name)
        db.session.add(player)
    country = str(raw.get("countryAcr") or "").upper()
    if country and not player.country:
        player.country = country[:3]
    if external_id:
        mapping = db.session.scalar(select(PlayerExternalId).where(
            PlayerExternalId.provider == PROVIDER,
            PlayerExternalId.external_id == external_id,
        ))
        if mapping is None:
            db.session.add(PlayerExternalId(player=player, provider=PROVIDER, external_id=external_id))
    return player


def ensure_edition(tour, tournament_name, tournament_data, played_on, level, surface):
    slug = tournament_slug(tournament_name)
    tournament = db.session.scalar(select(Tournament).where(
        Tournament.tour == tour, Tournament.slug == slug,
    ))
    if tournament is None:
        profile = tournament_profile(tournament_name)
        country = str(tournament_data.get("countryAcr") or "").upper()[:3] or None
        tournament = Tournament(
            tour=tour, slug=slug, name=tournament_name,
            location=profile.get("location"), country=country,
        )
        db.session.add(tournament)
        db.session.flush()
    edition = db.session.scalar(select(TournamentEdition).where(
        TournamentEdition.tournament_id == tournament.id,
        TournamentEdition.season == played_on.year,
    ))
    if edition is None:
        starts = parse_datetime(tournament_data.get("date"))
        edition = TournamentEdition(
            tournament=tournament, season=played_on.year, level=level, surface=surface,
            starts_on=starts.date() if starts else played_on, status="finished",
        )
        db.session.add(edition)
        db.session.flush()
    return edition


def equivalent_match(tour, played_on, tournament, winner, loser):
    rows = db.session.scalars(
        select(Match).where(
            Match.tour == tour,
            Match.week_start == played_on,
            func.lower(Match.tournament) == tournament.casefold(),
        )
    ).all()
    wanted = {normalized_name(winner.name), normalized_name(loser.name)}
    return next(
        (item for item in rows if {normalized_name(item.winner.name), normalized_name(item.loser.name)} == wanted),
        None,
    )


def provider_match_id(tour, value):
    key = f"{PROVIDER}:{tour.lower()}:{value}"
    if len(key) <= 80:
        return key
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return f"{PROVIDER}:{tour.lower()}:{digest}"


def import_result(tour, row):
    provider_value = str(row.get("matchId") or row.get("id") or "").strip()
    score = str(row.get("result") or "").strip()
    played_at = parse_datetime(row.get("date"))
    tournament_data = row.get("tournament") or {}
    tournament_name = str(tournament_data.get("name") or "").strip()
    winner_data = row.get("player1") or {}
    loser_data = row.get("player2") or {}
    if not provider_value or not score or not played_at or not tournament_name:
        return False
    if not winner_data.get("name") or not loser_data.get("name") or "/" in winner_data["name"] or "/" in loser_data["name"]:
        return False
    provider_id = provider_match_id(tour, provider_value)
    if db.session.scalar(select(Match.id).where(Match.provider_id == provider_id)):
        return False

    winner = ensure_player(tour, winner_data)
    loser = ensure_player(tour, loser_data)
    db.session.flush()
    played_on = played_at.date()
    existing = equivalent_match(tour, played_on, tournament_name, winner, loser)
    if existing:
        return False

    surface = surface_from(tournament_data)
    level = level_from(tour, tournament_data)
    edition = ensure_edition(tour, tournament_name, tournament_data, played_on, level, surface)
    match = Match(
        id=f"ta-{tour.lower()}-{provider_value}"[:100],
        provider=PROVIDER,
        provider_id=provider_id,
        status="finished",
        tour=tour,
        tournament=tournament_name[:120],
        level=level,
        surface=surface,
        week_start=played_on,
        scheduled_at=played_at,
        completed_at=played_at,
        edition=edition,
        round=ROUND_NAMES.get(as_int(row.get("roundId")), "R?")[:4],
        winner=winner,
        loser=loser,
        score=score[:120],
        best_of=as_int(row.get("best_of")),
    )
    db.session.add(match)
    db.session.flush()
    db.session.add_all((
        MatchParticipant(match_id=match.id, player_id=winner.id, side=1, is_winner=True),
        MatchParticipant(match_id=match.id, player_id=loser.id, side=2, is_winner=False),
    ))
    return True


class TennisApiClient:
    def __init__(self, session=requests, max_requests=48):
        self.session = session
        self.max_requests = max_requests
        self.requests_used = 0
        self.key = os.environ.get("RAPIDAPI_TENNIS_KEY", "").strip()
        if not self.key:
            raise RuntimeError("RAPIDAPI_TENNIS_KEY is not configured.")

    def results(self, tour, start, end, page=1):
        if self.requests_used >= self.max_requests:
            raise RequestBudgetExhausted
        response = self.session.get(
            f"{API_ROOT}/{tour.lower()}/results/{start.isoformat()}/{end.isoformat()}",
            params={"pageSize": PAGE_SIZE, "pageNo": page, "filter": "PlayerGroup:singles"},
            headers={
                "X-RapidAPI-Key": self.key,
                "X-RapidAPI-Host": API_HOST,
                "User-Agent": "Tennisd/1.0",
            },
            timeout=30,
        )
        self.requests_used += 1
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Tennis API returned an invalid response.")
        rows = payload.get("data") or []
        if not isinstance(rows, list):
            raise RuntimeError("Tennis API result data is not a list.")
        return rows, bool(payload.get("hasNextPage"))


def cursor_for(tour, yesterday):
    feed = f"history_{tour.lower()}"
    cursor = db.session.get(IngestionCursor, (PROVIDER, feed))
    if cursor is None:
        state = {"end": (yesterday - timedelta(days=2)).isoformat(), "page": 1}
        cursor = IngestionCursor(provider=PROVIDER, feed=feed, value=json.dumps(state))
        db.session.add(cursor)
        db.session.flush()
    else:
        state = json.loads(cursor.value)
    return cursor, state


def import_page(client, tour, start, end, page):
    rows, has_next = client.results(tour, start, end, page)
    written = 0
    for row in rows:
        written += int(import_result(tour, row))
    return len(rows), written, has_next


def import_range(client, tour, start, end, page=1):
    seen = written = 0
    has_next = True
    while has_next and client.requests_used < client.max_requests:
        rows_seen, rows_written, has_next = import_page(client, tour, start, end, page)
        seen += rows_seen
        written += rows_written
        page += 1
    return seen, written


def sync_recent_and_history(session=requests, today=None, max_requests=48):
    """Refresh recent finals, then spend the remaining free quota on 2010+ history."""
    today = today or datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    client = TennisApiClient(session=session, max_requests=max(2, min(int(max_requests), 50)))
    run = IngestionRun(provider=PROVIDER)
    db.session.add(run)
    db.session.commit()
    run_id = run.id
    seen = written = 0
    try:
        # Re-read a short recent window so late corrections are discovered safely.
        for tour in ("ATP", "WTA"):
            rows_seen, rows_written = import_range(
                client, tour, yesterday - timedelta(days=1), yesterday, 1,
            )
            seen += rows_seen
            written += rows_written
            db.session.commit()
            if client.requests_used >= client.max_requests:
                break

        while client.requests_used < client.max_requests:
            progressed = False
            for tour in ("ATP", "WTA"):
                if client.requests_used >= client.max_requests:
                    break
                cursor, state = cursor_for(tour, yesterday)
                end = date.fromisoformat(state["end"])
                if end < BACKFILL_FLOOR:
                    continue
                start = max(BACKFILL_FLOOR, end - timedelta(days=BACKFILL_DAYS - 1))
                page = int(state.get("page", 1))
                rows_seen, rows_written, has_next = import_page(client, tour, start, end, page)
                seen += rows_seen
                written += rows_written
                cursor.value = json.dumps(
                    {"end": end.isoformat(), "page": page + 1}
                    if has_next else {"end": (start - timedelta(days=1)).isoformat(), "page": 1}
                )
                db.session.commit()
                progressed = True
            if not progressed:
                break
        run.status = "finished"
    except Exception as error:
        db.session.rollback()
        run = db.session.get(IngestionRun, run_id)
        run.status = "failed"
        run.error = str(error)[:1000]
        raise
    finally:
        run.records_seen = seen
        run.records_written = written
        run.finished_at = datetime.now(timezone.utc)
        db.session.commit()
    return {"requests": client.requests_used, "seen": seen, "written": written}
