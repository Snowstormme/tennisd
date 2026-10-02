"""Budget-aware current tennis odds import from The Odds API."""

import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import select

from . import db
from .models import LiveMatch, LiveOdds

API_ROOT = "https://api.the-odds-api.com/v4"
BOOKMAKER = "pinnacle"


def normalized_name(value):
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def implied_probabilities(price1, price2):
    """Remove the bookmaker margin and return fair percentages that total 100."""
    if price1 <= 1 or price2 <= 1:
        raise ValueError("Decimal prices must be greater than 1")
    inverse1, inverse2 = 1 / price1, 1 / price2
    total = inverse1 + inverse2
    return round(inverse1 / total * 100, 1), round(inverse2 / total * 100, 1)


def parse_instant(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def active_tennis_keys(session=requests, api_key=None):
    response = session.get(
        f"{API_ROOT}/sports", params={"apiKey": api_key},
        headers={"User-Agent": "Tennisd/1.0"}, timeout=20,
    )
    response.raise_for_status()
    rows = response.json()
    return sorted(
        row["key"] for row in rows
        if row.get("active") and row.get("group") == "Tennis" and row.get("key")
    )


def selected_sport_key(keys, now):
    """Rotate one competition per 90-minute run to stay inside 500 credits/month."""
    if not keys:
        return None
    slot = int(now.timestamp() // (90 * 60))
    return keys[slot % len(keys)]


def _live_match_index(now):
    rows = db.session.scalars(
        select(LiveMatch).where(
            LiveMatch.status.in_(("live", "upcoming")),
            LiveMatch.starts_at >= now - timedelta(hours=12),
            LiveMatch.starts_at <= now + timedelta(days=7),
        )
    ).all()
    return {
        frozenset((normalized_name(row.player1_name), normalized_name(row.player2_name))): row
        for row in rows
    }


def _extract_quote(event, match, now):
    for bookmaker in event.get("bookmakers") or []:
        if bookmaker.get("key") != BOOKMAKER:
            continue
        market = next((item for item in bookmaker.get("markets") or [] if item.get("key") == "h2h"), None)
        if not market:
            continue
        prices = {
            normalized_name(outcome.get("name")): outcome.get("price")
            for outcome in market.get("outcomes") or []
        }
        try:
            price1 = float(prices[normalized_name(match.player1_name)])
            price2 = float(prices[normalized_name(match.player2_name)])
            probability1, probability2 = implied_probabilities(price1, price2)
        except (KeyError, TypeError, ValueError):
            continue
        return {
            "provider_event_id": str(event.get("id") or ""),
            "provider": "the_odds_api",
            "bookmaker": BOOKMAKER,
            "market": "h2h",
            "player1_price": price1,
            "player2_price": price2,
            "player1_probability": probability1,
            "player2_probability": probability2,
            "source_updated_at": parse_instant(bookmaker.get("last_update") or market.get("last_update")),
            "fetched_at": now,
        }
    return None


def sync_odds(session=requests, now=None):
    """Use one paid API request and attach returned h2h prices to existing live matches."""
    now = now or datetime.now(timezone.utc)
    key = os.environ.get("THE_ODDS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("THE_ODDS_API_KEY is not configured.")
    sport_key = selected_sport_key(active_tennis_keys(session, key), now)
    if not sport_key:
        return {"sport": None, "events": 0, "written": 0}
    response = session.get(
        f"{API_ROOT}/sports/{sport_key}/odds",
        params={
            "apiKey": key, "bookmakers": BOOKMAKER, "markets": "h2h",
            "oddsFormat": "decimal", "dateFormat": "iso",
        },
        headers={"User-Agent": "Tennisd/1.0"}, timeout=20,
    )
    response.raise_for_status()
    events = response.json()
    match_index = _live_match_index(now)
    written = 0
    for event in events if isinstance(events, list) else []:
        pair = frozenset((normalized_name(event.get("home_team")), normalized_name(event.get("away_team"))))
        match = match_index.get(pair)
        if match is None:
            continue
        values = _extract_quote(event, match, now)
        if not values:
            continue
        quote = db.session.scalar(select(LiveOdds).where(
            LiveOdds.live_match_id == match.provider_id,
            LiveOdds.bookmaker == BOOKMAKER,
            LiveOdds.market == "h2h",
        ))
        if quote is None:
            quote = LiveOdds(live_match_id=match.provider_id)
            db.session.add(quote)
        for field, value in values.items():
            setattr(quote, field, value)
        written += 1
    db.session.commit()
    return {"sport": sport_key, "events": len(events) if isinstance(events, list) else 0, "written": written}
