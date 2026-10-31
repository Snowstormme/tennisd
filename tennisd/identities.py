"""Conservative identity checks shared by ingestion and public catalog views."""

import re
import unicodedata
from collections import defaultdict

from sqlalchemy import and_, func, select
from sqlalchemy.orm import joinedload

from . import db
from .models import Match, Player, PlayerExternalId


LIVE_PROVIDER = "livetennisapi"


def normalized_player_name(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def player_identity_matches(player, tour, name):
    return bool(
        player and player.tour == tour
        and normalized_player_name(player.name) == normalized_player_name(name)
    )


class PlayerIdentityIndex:
    """Resolve a batch without collapsing namesakes or conflicting source IDs.

    An external ID is stronger evidence than a name. A name-only match is safe
    only when it is unique in its tour and neither the batch nor the stored
    mappings associate it with another ID from this provider.
    """

    def __init__(self, participants):
        self.by_name = defaultdict(dict)
        self.by_external = {}
        self.player_external_ids = defaultdict(set)
        self.incoming_ids = defaultdict(set)
        participants = list(participants)
        external_ids = {external_id for _, _, external_id in participants if external_id}
        names = {name.lower() for _, name, _ in participants if name}
        players = db.session.scalars(select(Player).where(func.lower(Player.name).in_(names))).all() if names else []
        for player in players:
            self.add_player(player)
        rows = db.session.scalars(
            select(PlayerExternalId).where(
                PlayerExternalId.provider == LIVE_PROVIDER,
                (PlayerExternalId.external_id.in_(external_ids))
                | (PlayerExternalId.player_id.in_([player.id for player in players])),
            ).options(joinedload(PlayerExternalId.player))
        ).all() if external_ids or players else []
        for row in rows:
            self.add_mapping(row.external_id, row.player)
        for tour, name, external_id in participants:
            if external_id:
                self.incoming_ids[(tour, normalized_player_name(name))].add(external_id)

    def add_player(self, player):
        self.by_name[(player.tour, normalized_player_name(player.name))][player.id] = player

    def add_mapping(self, external_id, player):
        self.by_external[external_id] = player
        self.player_external_ids[player.id].add(external_id)
        self.add_player(player)

    def resolve(self, tour, name, external_id):
        if external_id in self.by_external:
            player = self.by_external[external_id]
            return player if player_identity_matches(player, tour, name) else None
        key = (tour, normalized_player_name(name))
        candidates = list(self.by_name[key].values())
        if len(candidates) != 1:
            return None
        player = candidates[0]
        if external_id and (
            self.player_external_ids[player.id] - {external_id}
            or self.incoming_ids[key] - {external_id}
        ):
            return None
        return player


def profiles_for_matches(matches):
    matches = list(matches)
    participants = [
        (match.tour, getattr(match, f"player{side}_name"), getattr(match, f"player{side}_provider_id"))
        for match in matches for side in (1, 2)
    ]
    index = PlayerIdentityIndex(participants)
    return {
        match.provider_id: tuple(
            index.resolve(match.tour, getattr(match, f"player{side}_name"), getattr(match, f"player{side}_provider_id"))
            for side in (1, 2)
        )
        for match in matches
    }


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
