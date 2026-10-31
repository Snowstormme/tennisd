"""Prepare source-backed proposals without changing a single database row."""

import hashlib
import json
from datetime import datetime, timezone

import requests
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from . import db
from .catalog_audit import identity_reasons, match_snapshot
from .identities import PlayerIdentityIndex, catalog_identity_is_valid, player_identity_matches
from .live_tennis import API_ROOT, fetch_match_detail, live_player_id, normalize_history_match, score_from_winner_side
from .models import Match, Player


def _profile_proposal(index, row, side):
    name, external_id = row[f"player{side}_name"], row[f"player{side}_provider_id"]
    mapped = index.by_external.get(external_id)
    player = index.resolve(row["tour"], name, external_id)
    if mapped is not None and player is None:
        return {"name": name, "external_id": external_id, "blocked": "conflicting_external_id", "stored_player_id": mapped.id}
    if player is not None:
        return {"name": name, "external_id": external_id, "player_id": player.id, "action": "keep_profile"}
    if not external_id:
        return {"name": name, "blocked": "no_unambiguous_source_identity"}
    identifier = live_player_id(row["tour"], external_id, name)
    existing = db.session.get(Player, identifier)
    if existing is not None and not player_identity_matches(existing, row["tour"], name):
        return {"name": name, "external_id": external_id, "blocked": "conflicting_local_id", "stored_player_id": identifier}
    return {
        "name": name, "external_id": external_id, "player_id": identifier,
        "action": "keep_profile" if existing else "create_profile",
    }


def recovery_plan(*, limit=10, match_ids=(), session=requests, now=None):
    """Bound the sample and API calls; never revive a retired data integration.

    The output is review evidence, not an executable migration. In particular,
    a conflicting external-ID mapping is reported, never reassigned globally.
    """
    limit = max(1, min(int(limit), 50))
    now = now or datetime.now(timezone.utc)
    statement = select(Match).where(~catalog_identity_is_valid(include_status=False))
    if match_ids:
        statement = statement.where(Match.id.in_(match_ids))
    matches = db.session.scalars(statement.options(joinedload(Match.winner), joinedload(Match.loser))
        .order_by(Match.provider != "livetennisapi", Match.id).limit(limit)).all()
    proposals = []
    calls = 0
    stopped = None
    for match in matches:
        proposal = {"before": match_snapshot(match), "reasons": identity_reasons(match)}
        proposals.append(proposal)
        if match.provider != "livetennisapi":
            proposal["decision"] = "needs_original_source"
            continue
        prefix = "livetennisapi:"
        if not match.provider_id or not match.provider_id.startswith(prefix) or not match.provider_id[len(prefix):]:
            proposal["decision"] = "invalid_source_reference"
            continue
        if stopped:
            proposal["decision"] = "provider_requests_stopped"
            continue
        source_id = match.provider_id[len(prefix):]
        calls += 1
        try:
            detail = fetch_match_detail(source_id, session)
        except (requests.RequestException, ValueError, RuntimeError) as error:
            # Do not put API keys, request headers, bodies or database URLs in
            # a public report. Even a missing-key error stops further calls.
            stopped = type(error).__name__
            proposal.update(decision="provider_unavailable", error=stopped)
            continue
        if detail is None:
            proposal["decision"] = "source_not_found"
            continue
        proposal["evidence"] = {
            "url": f"{API_ROOT}/matches/{requests.utils.quote(source_id, safe='')}",
            "fetched_at": now.isoformat(),
            "sha256": hashlib.sha256(json.dumps(detail, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        }
        row = normalize_history_match(detail, now)
        if row is None:
            proposal["decision"] = "no_confirmed_singles_result"
            continue
        index = PlayerIdentityIndex([
            (row["tour"], row[f"player{side}_name"], row[f"player{side}_provider_id"]) for side in (1, 2)
        ])
        players = {side: _profile_proposal(index, row, side) for side in (1, 2)}
        proposal["source_result"] = {
            "tour": row["tour"], "tournament": row["tournament"], "date": row["starts_at"].date().isoformat(),
            "outcome": row["outcome"], "score": score_from_winner_side(row["score"], row["winner_side"]),
            "winner": players[row["winner_side"]], "loser": players[3 - row["winner_side"]],
        }
        proposal["decision"] = "blocked_identity_mapping" if any("blocked" in player for player in players.values()) else "ready_for_review"
    return {
        "schema_version": 1, "mode": "read_only_preview", "created_at": now.isoformat(),
        "sample_limit": limit, "sampled_matches": len(matches), "provider_requests": calls,
        "provider_requests_stopped": stopped, "proposals": proposals,
    }
