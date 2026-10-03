"""Optional, source-linked career prize money from Wikidata (property P2121)."""

from datetime import date, datetime, timedelta, timezone

import requests

from . import db


def update_prize_money(player):
    if not player.wikidata_id:
        return
    now = datetime.now(timezone.utc)
    checked = player.prize_checked_at
    if checked and now - checked.replace(tzinfo=timezone.utc) < timedelta(days=7):
        return

    try:
        response = requests.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbgetentities",
                "ids": player.wikidata_id,
                "props": "claims",
                "format": "json",
            },
            headers={"User-Agent": "Tennisd/1.0 (tennis diary; contact via site owner)"},
            timeout=4,
        )
        response.raise_for_status()
        claims = response.json()["entities"][player.wikidata_id].get("claims", {})
    except (requests.RequestException, KeyError, ValueError, TypeError):
        return

    statements = claims.get("P2121", [])
    statements = sorted(statements, key=lambda item: item.get("rank") != "preferred")
    for statement in statements:
        value = statement.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if value.get("unit") != "http://www.wikidata.org/entity/Q4917":
            continue  # Only show USD; never silently relabel another currency.
        try:
            amount = int(float(value["amount"]))
            asof = statement.get("qualifiers", {}).get("P813", [])[0]["datavalue"]["value"]["time"]
            player.prize_money_asof = date.fromisoformat(asof[1:11])
        except (KeyError, IndexError, ValueError, TypeError):
            player.prize_money_asof = None
            try:
                amount = int(float(value["amount"]))
            except (KeyError, ValueError, TypeError):
                continue
        if amount >= 0:
            player.prize_money_usd = amount
            break
    player.prize_checked_at = now
    db.session.commit()
