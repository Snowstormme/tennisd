"""Conservative identity checks shared by ingestion and public catalog views."""

import re
import unicodedata

from sqlalchemy import and_

from .models import Match, Player


def normalized_player_name(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def player_identity_matches(player, tour, name):
    return bool(
        player and player.tour == tour
        and normalized_player_name(player.name) == normalized_player_name(name)
    )


def catalog_identity_is_valid(*, include_status=True):
    """Hide inconsistent rows from discovery without deleting personal diaries."""
    return and_(
        Match.winner_id != Match.loser_id,
        Match.winner.has(Player.tour == Match.tour),
        Match.loser.has(Player.tour == Match.tour),
        Match.status == "finished" if include_status else True,
    )


def match_identity_is_valid(match):
    return bool(
        match.winner_id != match.loser_id
        and match.winner.tour == match.tour == match.loser.tour
        and match.status == "finished"
    )
