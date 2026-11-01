"""Small, transparent summaries calculated from the matches loaded into the catalog."""

from collections import Counter
from sqlalchemy import and_, case, func, or_, select, union_all

from . import db
from .models import Match, Player, Review
from .identities import catalog_is_visible


def percent(part, total):
    return round(part / total * 100) if total else None


def player_match_scope(player_id):
    return and_(catalog_is_visible(), or_(Match.winner_id == player_id, Match.loser_id == player_id))


def player_statistics(player):
    """Summarize the whole visible catalog; hydrate at most twelve titles."""
    won = Match.winner_id == player.id
    scope = player_match_scope(player.id)
    title = and_(won, Match.round == "F", Match.level.in_(("G", "M", "PM", "P", "A", "I", "F")))
    def own(winner, loser):
        return case((won, winner), else_=loser)
    base = select(
        Match.id, Match.week_start, Match.surface, Match.level,
        case((won, 1), else_=0).label("won"),
        case((title, 1), else_=0).label("title"),
        own(Match.loser_id, Match.winner_id).label("opponent_id"),
        own(Match.winner_rank, Match.loser_rank).label("rank"),
        *(own(getattr(Match, "w_" + name), getattr(Match, "l_" + name)).label(name)
          for name in ("ace", "first_in", "svpt", "first_won", "bp_saved", "bp_faced")),
    ).where(scope).subquery()
    c = base.c
    def total(value):
        return func.coalesce(func.sum(value), 0)
    ace_ok = c.ace >= 0
    metrics = {
        "first_serve": (c.first_in, c.svpt),
        "first_serve_won": (c.first_won, c.first_in),
        "break_saved": (c.bp_saved, c.bp_faced),
    }
    values = [func.count().label("match_count"), total(c.won).label("wins"),
              total(c.title).label("title_count"),
              total(case((and_(c.title == 1, c.level == "G"), 1), else_=0)).label("slam_titles"),
              total(case((ace_ok, c.ace), else_=0)).label("aces"),
              total(case((ace_ok, 1), else_=0)).label("ace_matches")]
    for name, (numerator, denominator) in metrics.items():
        valid = and_(numerator >= 0, denominator > 0, numerator <= denominator)
        values.extend((total(case((valid, numerator), else_=0)).label(name + "_num"),
                       total(case((valid, denominator), else_=0)).label(name + "_den"),
                       total(case((valid, 1), else_=0)).label(name + "_matches")))
    summary = dict(db.session.execute(select(*values).select_from(base)).one()._mapping)
    count, wins = summary["match_count"], summary["wins"]
    result = {
        "match_count": count, "wins": wins, "losses": count - wins,
        "win_pct": percent(wins, count), "title_count": summary["title_count"],
        "slam_titles": summary["slam_titles"], "titles": [], "surfaces": {}, "seasons": [],
        "head_to_head": [], "recent_rank": None,
        "aces_per_match": round(summary["aces"] / summary["ace_matches"], 1) if summary["ace_matches"] else None,
        "ace_matches": summary["ace_matches"],
    }
    for name in metrics:
        result[name + "_pct"] = percent(summary[name + "_num"], summary[name + "_den"])
        result[name + "_matches"] = summary[name + "_matches"]
    if not count:
        return result
    def record(n, w):
        return {"wins": w, "losses": n - w}
    result["surfaces"] = {surface: record(n, w) for surface,n,w in db.session.execute(
        select(c.surface, func.count(), total(c.won)).group_by(c.surface).order_by(c.surface))}
    year = func.extract("year", c.week_start)
    result["seasons"] = [(int(y), record(n,w)) for y,n,w in db.session.execute(
        select(year, func.count(), total(c.won)).group_by(year).order_by(year.desc()).limit(8))]
    opponent_count = func.count()
    result["head_to_head"] = [{"player": opponent, **record(n,w)} for opponent,n,w in db.session.execute(
        select(Player, opponent_count, total(c.won)).join(base, c.opponent_id == Player.id)
        .group_by(Player.id).order_by(opponent_count.desc(), Player.name, Player.id).limit(5))]
    rank = db.session.execute(select(c.rank, c.week_start).where(c.rank.is_not(None), c.rank > 0)
        .order_by(c.week_start.desc(), c.id.desc()).limit(1)).first()
    if rank:
        result["recent_rank"] = {"rank": rank[0], "week": rank[1]}
    if result["title_count"]:
        result["titles"] = db.session.scalars(select(Match).where(scope, title)
            .order_by(Match.week_start.desc(), Match.id.desc()).limit(12)).all()
    return result


def community_statistics(match):
    rows = db.session.execute(select(Review.rating_half, func.count(Review.id))
        .where(Review.match_id == match.id, Review.is_public.is_(True))
        .group_by(Review.rating_half)).all()
    counts = dict(rows)
    rated = sum(count for rating, count in rows if rating is not None)
    total = sum(rating * count for rating, count in rows if rating is not None)
    return {
        "average": round(total / rated, 1) if rated else None,
        "count": rated,
        "logs": sum(counts.values()),
        "distribution": {score: counts.get(score, 0) for score in range(10, 0, -1)},
    }


def diary_scope(user_id, *, public_only):
    """Share the same visibility condition across cards, totals and summaries."""
    return and_(Review.user_id == user_id, Review.is_public.is_(True) if public_only else True)


def diary_statistics(user_id, *, public_only=False):
    """Aggregate the whole visible diary without hydrating review bodies."""
    scope = diary_scope(user_id, public_only=public_only)
    logs, rated, rating_sum, favorites = db.session.execute(select(
        func.count(Review.id), func.count(Review.rating_half),
        func.coalesce(func.sum(Review.rating_half), 0),
        func.coalesce(func.sum(case((Review.is_favorite.is_(True), 1), else_=0)), 0),
    ).where(scope)).one()
    surfaces, tours = Counter(), Counter()
    for surface, tour, count in db.session.execute(select(Match.surface, Match.tour, func.count(Review.id))
        .join(Review, Review.match_id == Match.id).where(scope).group_by(Match.surface, Match.tour)):
        surfaces[surface] += count
        tours[tour] += count
    appearances = union_all(
        select(Match.winner_id.label("player_id")).join(Review, Review.match_id == Match.id).where(scope),
        select(Match.loser_id.label("player_id")).join(Review, Review.match_id == Match.id).where(scope),
    ).subquery()
    count = func.count()
    players = db.session.execute(select(Player.id, Player.name, count)
        .join(appearances, appearances.c.player_id == Player.id)
        .group_by(Player.id, Player.name).order_by(count.desc(), Player.name, Player.id).limit(5)).all()
    ratings = dict(db.session.execute(select(Review.rating_half, func.count(Review.id))
        .where(scope, Review.rating_half.is_not(None)).group_by(Review.rating_half)).all())
    year = func.extract("year", Review.watched_on)
    years = db.session.execute(select(year, func.count(Review.id)).where(scope)
        .group_by(year).order_by(func.count(Review.id).desc(), year.desc())).all()
    average = round(rating_sum / rated, 1) if rated else None
    return {
        "logs": logs, "favorites": favorites, "average": average, "average_ten": average,
        "surfaces": sorted(surfaces.items(), key=lambda item: (-item[1], item[0])),
        "tours": sorted(tours.items(), key=lambda item: (-item[1], item[0])),
        "players": [{"id": player_id, "name": name, "count": total} for player_id, name, total in players],
        "years": [(int(year), total) for year, total in years],
        "rating_bands": [(score, ratings.get(score, 0)) for score in range(10, 0, -1)],
        "rating_scale": [(score, ratings.get(score, 0)) for score in range(1, 11)],
        "rated_count": rated,
    }
