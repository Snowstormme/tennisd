"""Transparent tournament-based baseline for match recommendations.

This module deliberately contains no trained model.  It provides the rule-based
reference that an ML ranker must beat.  It accepts ordinary objects with the
same attributes as ``TournamentSubscription`` and ``LiveMatch``, so it stays
easy to test and does not depend on a Flask application context.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import log2
from typing import Iterable, Mapping


SUBSCRIPTION_BONUS = 100.0
LIVE_BONUS = 15.0
POPULARITY_MULTIPLIER = 5.0
POPULARITY_CAP = 25.0
START_TIME_BONUS = 5.0
RECOMMENDATION_WINDOW = timedelta(days=7)

TIER_BONUSES = {
    "grand_slam": 20.0,
    "atp_1000": 15.0,
    "wta_1000": 15.0,
    "atp_500": 10.0,
    "wta_500": 10.0,
}


@dataclass(frozen=True)
class TournamentRecommendation:
    """One ranked candidate plus human-readable ranking reasons."""

    match: object
    score: float
    reasons: tuple[str, ...]


def tournament_key(tour: str, tournament: str) -> tuple[str, str]:
    """Create a stable comparison key for a tournament."""

    normalized_name = " ".join((tournament or "").casefold().split())
    return (tour or "").upper(), normalized_name


def _subscription_key(subscription: object) -> tuple[str, str]:
    if isinstance(subscription, tuple) and len(subscription) == 2:
        return tournament_key(subscription[0], subscription[1])
    return tournament_key(subscription.tour, subscription.tournament)


def tournament_popularity(
    subscriptions: Iterable[object],
) -> Counter[tuple[str, str]]:
    """Count followers for every ATP/WTA tournament."""

    return Counter(_subscription_key(subscription) for subscription in subscriptions)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _candidate_is_available(match: object, now: datetime) -> bool:
    if getattr(match, "is_doubles", False):
        return False
    status = (getattr(match, "status", "") or "").lower()
    if status == "live":
        return True
    if status != "upcoming":
        return False
    starts_at = _aware(getattr(match, "starts_at", None))
    return starts_at is not None and now - timedelta(hours=6) <= starts_at <= now + RECOMMENDATION_WINDOW


def _start_time_score(match: object, now: datetime) -> float:
    starts_at = _aware(getattr(match, "starts_at", None))
    if starts_at is None:
        return 0.0
    hours_until_start = max(0.0, (starts_at - now).total_seconds() / 3600)
    window_hours = RECOMMENDATION_WINDOW.total_seconds() / 3600
    return START_TIME_BONUS * max(0.0, 1.0 - hours_until_start / window_hours)


def recommend_tournament_matches(
    matches: Iterable[object],
    user_subscriptions: Iterable[object],
    all_subscriptions: Iterable[object],
    *,
    limit: int = 10,
    now: datetime | None = None,
) -> list[TournamentRecommendation]:
    """Rank available matches using tournament interest and popularity.

    Subscribed tournaments receive the strongest signal.  Global subscriber
    counts provide a cold-start fallback, while tournament tier and start time
    break ties without pretending that this baseline learned any weights.
    """

    if limit < 1:
        return []
    now = _aware(now) if now is not None else datetime.now(timezone.utc)
    subscribed_keys = {_subscription_key(item) for item in user_subscriptions}
    popularity: Mapping[tuple[str, str], int] = tournament_popularity(all_subscriptions)
    ranked: list[TournamentRecommendation] = []

    for match in matches:
        if not _candidate_is_available(match, now):
            continue

        key = tournament_key(match.tour, match.tournament)
        follower_count = popularity.get(key, 0)
        tier = (getattr(match, "tier", "") or "").lower()
        status = (getattr(match, "status", "") or "").lower()
        score = 0.0
        reasons: list[str] = []

        if key in subscribed_keys:
            score += SUBSCRIPTION_BONUS
            reasons.append("Subscribed tournament")
        if follower_count:
            score += min(POPULARITY_CAP, log2(follower_count + 1) * POPULARITY_MULTIPLIER)
            reasons.append(f"{follower_count} tournament follower{'s' if follower_count != 1 else ''}")
        if tier in TIER_BONUSES:
            score += TIER_BONUSES[tier]
            reasons.append(tier.replace("_", " ").title())
        if status == "live":
            score += LIVE_BONUS
            reasons.append("Live now")
        else:
            score += _start_time_score(match, now)
            reasons.append("Starting soon")

        ranked.append(TournamentRecommendation(match, round(score, 3), tuple(reasons)))

    far_future = now + timedelta(days=3650)
    ranked.sort(
        key=lambda item: (
            -item.score,
            _aware(getattr(item.match, "starts_at", None)) or far_future,
            str(getattr(item.match, "provider_id", "")),
        )
    )
    return ranked[:limit]
