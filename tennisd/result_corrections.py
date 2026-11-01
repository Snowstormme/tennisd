"""Explicit, source-reviewed corrections; no bulk guessing or player merging."""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select

from . import db
from .catalog_audit import match_snapshot
from .models import IngestionRun, Match, MatchParticipant, MatchStatistic, Player, PlayerExternalId, utcnow


@lru_cache(maxsize=1)
def definitions():
    rows = json.loads((Path(__file__).parent / "data" / "result_corrections.json").read_text())
    return {row["id"]: row for row in rows}


def corrected_snapshot(correction):
    result = deepcopy(correction["before"])
    result["loser"] = {key: correction["corrected_player"][key] for key in ("id", "name", "tour")}
    result["score"] = correction["corrected_score"]
    return result


def correction_for_match(match):
    for correction in definitions().values():
        if match.id == correction["before"]["id"] and match_snapshot(match) == corrected_snapshot(correction):
            return correction
    return None


def _reviewed_player(correction, *, lock=False):
    """Validate a reviewed target without merging or guessing by name."""
    data = correction["corrected_player"]
    player = db.session.get(Player, data["id"], with_for_update=lock)
    if player is not None and any(getattr(player, key) != data[key] for key in ("name", "tour", "country")):
        raise ValueError("The verified player ID already identifies a different profile.")
    if correction.get("use_existing_player"):
        if player is None:
            raise ValueError("The reviewed existing player profile is missing.")
        return player, None
    mapping = db.session.scalar(select(PlayerExternalId).where(
        PlayerExternalId.provider == "itf", PlayerExternalId.external_id == data["itf_id"],
    ))
    if mapping is not None and mapping.player_id != data["id"]:
        raise ValueError("The official ITF ID already identifies another profile.")
    return player, mapping


def reviewed_correction(correction_id, *, operation="preview"):
    """Change only reviewed result fields under a lock, with exact drift guards.

    Callers commit only after the complete operation succeeds. A rollback keeps
    the verified player profile, since other records might already refer to it.
    Neither operation deletes data or changes existing provider/player mappings.
    """
    if operation not in ("preview", "apply", "rollback"):
        raise ValueError("Unknown correction operation.")
    correction = definitions().get(correction_id)
    if correction is None:
        raise ValueError("Unknown reviewed correction.")
    match = db.session.get(Match, correction["before"]["id"], with_for_update=operation != "preview")
    if match is None:
        raise ValueError("The reviewed permanent match does not exist.")
    before, after = correction["before"], corrected_snapshot(correction)
    current = match_snapshot(match)
    if current not in (before, after):
        raise ValueError("Result fields changed since review; refuse to overwrite the newer record.")
    for model in (MatchParticipant, MatchStatistic):
        if db.session.scalar(select(func.count(model.id)).where(model.match_id == match.id)):
            raise ValueError("Related participant/statistic rows require an additional review.")
    wanted = before if operation == "rollback" else after
    # Preview must validate the target too, without creating a profile/mapping.
    if operation != "rollback":
        player, mapping = _reviewed_player(correction, lock=operation == "apply")
    changed = operation != "preview" and current != wanted
    if changed:
        if operation == "apply":
            data = correction["corrected_player"]
            if player is None:
                player = Player(**{key: data[key] for key in ("id", "name", "tour", "country")})
                db.session.add(player)
            if not correction.get("use_existing_player") and mapping is None:
                db.session.add(PlayerExternalId(player=player, provider="itf", external_id=data["itf_id"]))
        else:
            player = db.session.get(Player, before["loser"]["id"])
            if player is None or any(getattr(player, key) != before["loser"][key] for key in ("name", "tour")):
                raise ValueError("The original profile changed; refuse an unsafe rollback.")
        match.loser = player
        match.score = wanted["score"]
        db.session.add(IngestionRun(
            provider="catalog:curation", status="success", finished_at=utcnow(), records_seen=1, records_written=1,
        ))
        db.session.flush()
    return {
        "correction": correction_id, "operation": operation, "changed": changed,
        "before": current, "after": wanted if operation != "preview" else after,
        "evidence": correction["evidence"], "verified_on": correction["verified_on"],
    }
