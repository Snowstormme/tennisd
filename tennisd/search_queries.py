"""Literal text search shared by overview and complete result categories."""
from sqlalchemy import or_, select
from sqlalchemy.orm import aliased, joinedload
from .identities import catalog_is_visible
from .models import Match, Player, User


def contains_text(column, query):
    escaped = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return column.ilike(f"%{escaped}%", escape="!")


def search_statements(query):
    winner, loser = aliased(Player), aliased(Player)
    return {
        "players": select(Player).where(contains_text(Player.name, query)).order_by(Player.name, Player.id),
        "matches": select(Match).join(winner, Match.winner).join(loser, Match.loser)
            .where(catalog_is_visible(), or_(contains_text(Match.tournament, query),
                contains_text(winner.name, query), contains_text(loser.name, query)))
            .options(joinedload(Match.winner), joinedload(Match.loser))
            .order_by(Match.week_start.desc(), Match.id),
        "tournaments": select(Match.tour, Match.tournament)
            .where(catalog_is_visible(), contains_text(Match.tournament, query))
            .distinct().order_by(Match.tournament, Match.tour),
        "members": select(User).where(or_(contains_text(User.username, query), contains_text(User.display_name, query)))
            .order_by(User.username, User.id),
    }
