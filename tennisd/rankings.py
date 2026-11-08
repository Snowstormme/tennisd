"""Recorded singles positions; never substitute a doubles or invalid rank."""
from sqlalchemy import and_, func, select
from .models import RankingSnapshot


def valid_singles_ranking():
    return and_(RankingSnapshot.ranking_type == "singles", RankingSnapshot.rank > 0)


def latest_singles_dates(player_ids=None):
    statement = select(RankingSnapshot.player_id,
        func.max(RankingSnapshot.ranked_on).label("ranked_on")).where(valid_singles_ranking())
    if player_ids is not None:
        statement = statement.where(RankingSnapshot.player_id.in_(player_ids))
    return statement.group_by(RankingSnapshot.player_id).subquery()
