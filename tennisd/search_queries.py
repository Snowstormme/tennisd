"""Literal text search shared by overview and complete result categories."""
from sqlalchemy import or_, select, union
from sqlalchemy.orm import aliased, joinedload
from .identities import catalog_is_visible
from .models import Match, Player, ProfileImage, User


def contains_text(column, query):
    escaped = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return column.ilike(f"%{escaped}%", escape="!")


def search_statements(query):
    winner, loser = aliased(Player), aliased(Player)
    winner_search = aliased(Player)
    loser_search = aliased(Player)
    matching_match_ids = union(
        select(Match.id).where(contains_text(Match.tournament, query)),
        select(Match.id).where(Match.winner_id.in_(
            select(winner_search.id).where(contains_text(winner_search.name, query))
        )),
        select(Match.id).where(Match.loser_id.in_(
            select(loser_search.id).where(contains_text(loser_search.name, query))
        )),
    ).subquery()
    return {
        "players": select(Player).where(contains_text(Player.name, query)).order_by(Player.name, Player.id),
        "matches": select(Match).join(matching_match_ids, matching_match_ids.c.id == Match.id)
            .join(winner, Match.winner).join(loser, Match.loser)
            .where(catalog_is_visible())
            .options(joinedload(Match.winner), joinedload(Match.loser))
            .order_by(Match.week_start.desc(), Match.id),
        "tournaments": select(Match.tour, Match.tournament)
            .where(catalog_is_visible(), contains_text(Match.tournament, query))
            .distinct().order_by(Match.tournament, Match.tour),
        "members": select(User).where(or_(contains_text(User.username, query), contains_text(User.display_name, query)))
            .options(joinedload(User.profile_image).load_only(ProfileImage.user_id))
            .order_by(User.username, User.id),
    }
