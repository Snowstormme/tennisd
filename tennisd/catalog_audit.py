"""Read-only catalog diagnostics. Reports never include account or diary data."""

from contextlib import contextmanager
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from . import db
from .identities import catalog_identity_is_valid
from .models import IngestionRun, Match, Player


@contextmanager
def readonly_catalog_session():
    """Enforce read-only SQL for a standalone diagnostic transaction."""
    if db.session.new or db.session.dirty or db.session.deleted:
        raise RuntimeError("Start catalog diagnostics without pending database changes.")
    connection = db.session.connection()
    dialect = connection.dialect.name
    if dialect == "postgresql":
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
    elif dialect == "sqlite":
        connection.exec_driver_sql("PRAGMA query_only = ON")
    else:
        raise RuntimeError("Read-only catalog diagnostics support PostgreSQL and SQLite only.")
    try:
        yield
    finally:
        if dialect == "sqlite":
            connection.exec_driver_sql("PRAGMA query_only = OFF")
        db.session.rollback()


def identity_reasons(match):
    reasons = []
    if not match.winner or not match.loser:
        reasons.append("missing_player")
    if match.winner_id == match.loser_id:
        reasons.append("same_player")
    if any(player and player.tour != match.tour for player in (match.winner, match.loser)):
        reasons.append("cross_tour")
    return reasons


def match_snapshot(match, *, include_round=False):
    """Public result fields needed to compare a proposal with its original row."""
    result = {
        "id": match.id, "provider": match.provider, "provider_id": match.provider_id,
        "tour": match.tour, "tournament": match.tournament, "status": match.status,
        "date": match.week_start.isoformat(), "score": match.score,
        "winner": {"id": match.winner_id, "name": match.winner.name, "tour": match.winner.tour} if match.winner else None,
        "loser": {"id": match.loser_id, "name": match.loser.name, "tour": match.loser.tour} if match.loser else None,
    }
    if include_round:
        result["round"] = match.round
    return result


def catalog_cohort_report(*, limit=50, tour="", year=None, tournament="", provider="", after_id=""):
    """Inspect a bounded public cohort, including rows with valid identities.

    Totals describe the entire selected cohort; only samples use the cursor.
    Tournament names are exact, case-insensitive matches, not SQL patterns.
    Round distributions are observations, never inferred correctness labels.
    """
    if not 1 <= limit <= 500:
        raise ValueError("Sample limit must be between 1 and 500.")
    if tour not in ("", "ATP", "WTA"):
        raise ValueError("Tour must be ATP or WTA.")
    if year is not None and (not isinstance(year, int) or not 1968 <= year <= 2100):
        raise ValueError("Year must be between 1968 and 2100.")
    tournament, provider, after_id = tournament.strip(), provider.strip(), after_id.strip()
    if len(tournament) > 120 or len(provider) > 32 or len(after_id) > 100:
        raise ValueError("Cohort filter exceeds its field length.")
    filters = []
    if tour:
        filters.append(Match.tour == tour)
    if year is not None:
        filters.extend((Match.week_start >= date(year, 1, 1), Match.week_start < date(year + 1, 1, 1)))
    if tournament:
        filters.append(func.lower(Match.tournament) == tournament.lower())
    if provider:
        filters.append(Match.provider == provider)
    total = db.session.scalar(select(func.count(Match.id)).where(*filters))
    invalid = db.session.scalar(select(func.count(Match.id)).where(
        *filters, ~catalog_identity_is_valid(include_status=False)))
    statement = select(Match).where(*filters).options(joinedload(Match.winner), joinedload(Match.loser))
    if after_id:
        statement = statement.where(Match.id > after_id)
    rows = db.session.scalars(statement.order_by(Match.id).limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    rounds = db.session.execute(select(Match.round, func.count(Match.id)).where(*filters)
        .group_by(Match.round).order_by(Match.round)).all()
    return {
        "schema_version": 1, "mode": "read_only_cohort",
        "scope": {"tour": tour or None, "year": year, "tournament": tournament or None, "provider": provider or None},
        "counts": {"matches": total, "identity_conflicts": invalid},
        "round_distribution": [{"round": code, "count": count} for code, count in rounds],
        "sample_limit": limit, "after_id": after_id or None, "sampled_matches": len(rows),
        "has_more": has_more, "next_after_id": rows[-1].id if has_more else None,
        "samples": [{**match_snapshot(row, include_round=True), "identity_reasons": identity_reasons(row)} for row in rows],
    }


def catalog_report(limit=50):
    limit = max(1, min(int(limit), 500))
    invalid = ~catalog_identity_is_valid(include_status=False)
    samples = db.session.scalars(
        select(Match).where(invalid).options(joinedload(Match.winner), joinedload(Match.loser))
        .order_by(Match.id).limit(limit)
    ).all()
    source_rows = db.session.execute(
        select(Match.provider, Match.tour, func.count(Match.id)).where(invalid)
        .group_by(Match.provider, Match.tour).order_by(Match.provider, Match.tour)
    ).all()
    latest_feeds = {}
    for feed in ("live", "upcoming"):
        run = db.session.scalar(select(IngestionRun).where(
            IngestionRun.provider == f"livetennisapi:{feed}",
        ).order_by(IngestionRun.started_at.desc(), IngestionRun.id.desc()).limit(1))
        latest_feeds[feed] = None if run is None else {
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "records_seen": run.records_seen, "records_written": run.records_written,
            "has_error": bool(run.error),
        }
    return {
        "schema_version": 2,
        "cross_tour_or_self_matches": db.session.scalar(select(func.count(Match.id)).where(invalid)),
        "invalid_by_source": [
            {"provider": provider, "tour": tour, "count": count} for provider, tour, count in source_rows
        ],
        "identity_reason_counts": {
            "same_player": db.session.scalar(select(func.count(Match.id)).where(Match.winner_id == Match.loser_id)),
            "cross_tour": db.session.scalar(select(func.count(Match.id)).where(
                Match.winner.has(Player.tour != Match.tour) | Match.loser.has(Player.tour != Match.tour),
            )),
            "missing_player": db.session.scalar(select(func.count(Match.id)).where(~Match.winner.has() | ~Match.loser.has())),
        },
        "invalid_match_ids": [match.id for match in samples],
        "invalid_match_samples": [{**match_snapshot(match), "reasons": identity_reasons(match)} for match in samples],
        "legacy_null_scores": db.session.scalar(select(func.count(Match.id)).where(
            Match.score.ilike("%None%") | Match.score.ilike("%null%"),
        )),
        "held_or_cancelled_results": db.session.scalar(select(func.count(Match.id)).where(Match.status != "finished")),
        "placeholder_players": db.session.scalar(select(func.count(Player.id)).where(
            func.lower(Player.name).in_(("unknown player", "unknown", "tbd", "tba")),
        )),
        "latest_feed_runs": latest_feeds,
    }
