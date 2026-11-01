"""Explicit, source-reviewed corrections; no bulk guessing or player merging."""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select

from . import db
from .catalog_audit import match_snapshot
from .models import IngestionRun, Match, MatchParticipant, MatchSet, MatchStatistic, Player, PlayerExternalId, utcnow


@lru_cache(maxsize=1)
def definitions():
    rows = json.loads((Path(__file__).parent / "data" / "result_corrections.json").read_text())
    return {row["id"]: row for row in rows}


def corrected_snapshot(correction):
    result = deepcopy(correction["before"])
    if correction.get("kind") == "round":
        if correction["corrected_round"] not in ("F", "SF", "QF", "R16", "R32", "R64", "R128", "RR", "Q1", "Q2", "Q3"):
            raise ValueError("The reviewed target round is not supported.")
        result["round"] = correction["corrected_round"]
        return result
    side = correction.get("corrected_side", "loser")
    if side not in ("winner", "loser"):
        raise ValueError("The reviewed correction must identify a winner or loser side.")
    other = "winner" if side == "loser" else "loser"
    player = correction["corrected_player"]
    if player["tour"] != result["tour"] or player["id"] == result[other]["id"]:
        raise ValueError("The corrected result must identify distinct players in the same tour.")
    result[side] = {key: player[key] for key in ("id", "name", "tour")}
    result["score"] = correction["corrected_score"]
    return result


def correction_for_match(match):
    # Prefer a later, more specific metadata review when both still match.
    for correction in reversed(tuple(definitions().values())):
        if match.id == correction["before"]["id"] and match_snapshot(
            match, include_round=correction.get("kind") == "round",
        ) == corrected_snapshot(correction):
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


def _related_rows(match_id, *, lock=False):
    rows, snapshot = {}, {}
    for name, model in (("participants", MatchParticipant), ("statistics", MatchStatistic), ("sets", MatchSet)):
        statement = select(model).where(model.match_id == match_id).order_by(model.id)
        if lock:
            statement = statement.with_for_update()
        rows[name] = db.session.scalars(statement).all()
        snapshot[name] = [{column.name: getattr(row, column.name) for column in model.__table__.columns}
                          for row in rows[name]]
    return rows, snapshot


def _corrected_related(correction):
    result = deepcopy(correction["related_before"])
    if correction.get("kind") == "round":
        return result
    for name in ("participants", "statistics"):
        for row in result[name]:
            if row["player_id"] == correction["before"][correction.get("corrected_side", "loser")]["id"]:
                row["player_id"] = correction["corrected_player"]["id"]
    return result


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
    round_only = correction.get("kind") == "round"
    side = correction.get("corrected_side", "loser")
    match = db.session.get(Match, correction["before"]["id"], with_for_update=operation != "preview")
    if match is None:
        raise ValueError("The reviewed permanent match does not exist.")
    before, after = correction["before"], corrected_snapshot(correction)
    current = match_snapshot(match, include_round=round_only)
    if current not in (before, after):
        raise ValueError("Result fields changed since review; refuse to overwrite the newer record.")
    related_rows, related = _related_rows(match.id, lock=operation != "preview")
    reviewed_related = correction.get("related_before")
    blocked = None
    if reviewed_related is None:
        if related["participants"] or related["statistics"]:
            blocked = "Related participant/statistic rows require an additional review."
    elif related != (reviewed_related if current == before else _corrected_related(correction)):
        raise ValueError("Related rows changed since review; refuse to overwrite newer data.")
    if blocked and operation != "preview":
        raise ValueError(blocked)
    wanted = before if operation == "rollback" else after
    # Preview must validate the target too, without creating a profile/mapping.
    if operation != "rollback" and not round_only:
        player, mapping = _reviewed_player(correction, lock=operation == "apply")
    if blocked:
        return {"correction": correction_id, "operation": operation, "changed": False,
                "blocked": blocked, "before": current, "after": after, "related_before": related,
                "evidence": correction["evidence"], "verified_on": correction["verified_on"]}
    changed = operation != "preview" and current != wanted
    if changed:
        if round_only:
            match.round = wanted["round"]
        elif operation == "apply":
            data = correction["corrected_player"]
            if player is None:
                player = Player(**{key: data[key] for key in ("id", "name", "tour", "country")})
                db.session.add(player)
            if not correction.get("use_existing_player") and mapping is None:
                db.session.add(PlayerExternalId(player=player, provider="itf", external_id=data["itf_id"]))
        else:
            player = db.session.get(Player, before[side]["id"])
            if player is None or any(getattr(player, key) != before[side][key] for key in ("name", "tour")):
                raise ValueError("The original profile changed; refuse an unsafe rollback.")
        if not round_only:
            setattr(match, side, player)
            match.score = wanted["score"]
        if reviewed_related is not None and not round_only:
            wanted_related = reviewed_related if operation == "rollback" else _corrected_related(correction)
            for name in ("participants", "statistics"):
                expected_by_id = {row["id"]: row for row in wanted_related[name]}
                for row in related_rows[name]:
                    row.player_id = expected_by_id[row.id]["player_id"]
        db.session.add(IngestionRun(
            provider="catalog:curation", status="success", finished_at=utcnow(), records_seen=1, records_written=1,
        ))
        db.session.flush()
    return {
        "correction": correction_id, "operation": operation, "changed": changed,
        "before": current, "after": wanted if operation != "preview" else after,
        "evidence": correction["evidence"], "verified_on": correction["verified_on"],
        "related_before": related,
        "related_after": (reviewed_related if operation == "rollback" else _corrected_related(correction))
                         if reviewed_related is not None else related,
    }


def batches():
    """Version-controlled groups only; callers cannot submit arbitrary match IDs."""
    result = {}
    for key, correction in definitions().items():
        if correction.get("batch"):
            result.setdefault(correction["batch"], []).append(key)
    return {name: sorted(keys, key=lambda key: definitions()[key]["before"]["id"])
            for name, keys in result.items()}


def reviewed_batch(batch_id, *, operation="preview"):
    """Apply a bounded reviewed group atomically; the caller commits once.

    All matches are locked in permanent-ID order. A savepoint rolls back this
    entire group on error, even if a caller catches it. No commit happens here.
    """
    keys = batches().get(batch_id)
    if not keys or len(keys) > 20:
        raise ValueError("Unknown or oversized reviewed correction batch.")
    if db.session.new or db.session.dirty or db.session.deleted:
        raise RuntimeError("Start a reviewed batch without pending database changes.")
    connection = db.session.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        # sqlite3 legacy transaction control does not BEGIN for SAVEPOINT.
        # Explicitly anchor it so releasing the savepoint cannot commit the
        # group before the caller's outer transaction is committed.
        connection.exec_driver_sql("BEGIN")
    with db.session.begin_nested():
        results = [reviewed_correction(key, operation=operation) for key in keys]
    return {"batch": batch_id, "operation": operation,
            "changed": sum(result["changed"] for result in results),
            "blocked": any(result.get("blocked") for result in results), "results": results}
