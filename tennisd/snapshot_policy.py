"""Current-feed visibility is independent of the provider's stored outcome."""
from datetime import timedelta, timezone

from sqlalchemy import and_, case, func, or_, select

from . import db
from .models import LiveMatch, utcnow

MAX_AGE = timedelta(hours=30)
CLOCK_SKEW = timedelta(minutes=5)
LIVE_LOOKBACK = timedelta(hours=72)
START_GRACE = timedelta(hours=6)
HORIZON = timedelta(days=7)


def as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def current_snapshot_condition(now=None):
    """Apply identical freshness/schedule bounds to all public current queries."""
    now = as_utc(now or utcnow())
    ended = func.coalesce(LiveMatch.finished_at, LiveMatch.starts_at)
    return and_(
        or_(LiveMatch.round.is_(None), LiveMatch.round != "R128"),
        LiveMatch.synced_at >= now - MAX_AGE, LiveMatch.synced_at <= now + CLOCK_SKEW,
        or_(
            and_(LiveMatch.status == "live", or_(LiveMatch.starts_at.is_(None), and_(
                LiveMatch.starts_at >= now - LIVE_LOOKBACK, LiveMatch.starts_at <= now + START_GRACE))),
            and_(LiveMatch.status == "upcoming", LiveMatch.starts_at >= now - START_GRACE,
                 LiveMatch.starts_at <= now + HORIZON),
            and_(LiveMatch.status == "finished", ended >= now - HORIZON, ended <= now + CLOCK_SKEW),
        ),
    )


def snapshot_visibility_reason(match, now=None):
    """Explain exclusion without rewriting status, score, winner or timestamps."""
    now = as_utc(now or utcnow())
    if match.round == "R128":
        return "This round is outside public browsing. The saved snapshot is preserved."
    checked = as_utc(match.synced_at) if match.synced_at else None
    if checked is None or checked < now - MAX_AGE:
        return "This snapshot has not been refreshed within the last 30 hours."
    if checked > now + CLOCK_SKEW:
        return "The recorded check time is outside the allowed clock window."
    start = as_utc(match.starts_at) if match.starts_at else None
    if match.status == "live":
        if start is None or now - LIVE_LOOKBACK <= start <= now + START_GRACE:
            return None
    elif match.status == "upcoming":
        if start is not None and now - START_GRACE <= start <= now + HORIZON:
            return None
    elif match.status == "finished":
        end = as_utc(match.finished_at) if match.finished_at else start
        if end is not None and now - HORIZON <= end <= now + CLOCK_SKEW:
            return None
    return "Its last reported status or schedule is outside the current-feed window."


def snapshot_counts(now=None):
    """Public diagnostics count observations, never infer match outcomes."""
    rows = db.session.execute(select(LiveMatch.status, func.count(),
        func.sum(case((current_snapshot_condition(now), 1), else_=0)))
        .where(LiveMatch.status.in_(("live", "upcoming", "finished")),
               or_(LiveMatch.round.is_(None), LiveMatch.round != "R128"))
        .group_by(LiveMatch.status)).all()
    counts = {status: {"current": 0, "outside_current_window": 0} for status in ("live", "upcoming", "finished")}
    for status, total, current in rows:
        counts[status] = {"current": current, "outside_current_window": total - current}
    return counts
