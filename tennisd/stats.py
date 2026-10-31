"""Small, transparent summaries calculated from the matches loaded into the catalog."""

from collections import Counter, defaultdict
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload

from . import db
from .models import Match, Review
from .identities import catalog_identity_is_valid


def percent(part, total):
    return round(part / total * 100) if total else None


def player_statistics(player):
    matches = db.session.scalars(
        select(Match)
        .where(catalog_identity_is_valid(), or_(Match.winner_id == player.id, Match.loser_id == player.id))
        .options(joinedload(Match.winner), joinedload(Match.loser))
        .order_by(Match.week_start.desc())
    ).all()
    wins = sum(match.winner_id == player.id for match in matches)
    surfaces = defaultdict(lambda: {"wins": 0, "losses": 0})
    seasons = defaultdict(lambda: {"wins": 0, "losses": 0})
    opponents = {}
    titles = []
    aces = 0
    ace_matches = 0
    first_in = 0
    serve_points = 0
    first_won = 0
    break_saved = 0
    break_faced = 0
    recent_rank = None

    for match in matches:
        won = match.winner_id == player.id
        result = "wins" if won else "losses"
        surfaces[match.surface][result] += 1
        seasons[match.week_start.year][result] += 1
        opponent = match.loser if won else match.winner
        if opponent.id not in opponents:
            opponents[opponent.id] = {"player": opponent, "wins": 0, "losses": 0}
        opponents[opponent.id][result] += 1
        # Team ties and legends events have a final round but are not singles titles.
        if won and match.round == "F" and match.level in {"G", "M", "PM", "P", "A", "I", "F"}:
            titles.append(match)

        prefix = "w" if won else "l"
        ace = getattr(match, f"{prefix}_ace")
        if ace is not None:
            aces += ace
            ace_matches += 1
        first_in += getattr(match, f"{prefix}_first_in") or 0
        serve_points += getattr(match, f"{prefix}_svpt") or 0
        first_won += getattr(match, f"{prefix}_first_won") or 0
        break_saved += getattr(match, f"{prefix}_bp_saved") or 0
        break_faced += getattr(match, f"{prefix}_bp_faced") or 0
        rank = match.winner_rank if won else match.loser_rank
        if recent_rank is None and rank is not None:
            recent_rank = {"rank": rank, "week": match.week_start}

    head_to_head = sorted(
        opponents.values(), key=lambda item: item["wins"] + item["losses"], reverse=True
    )[:5]
    return {
        "matches": matches,
        "wins": wins,
        "losses": len(matches) - wins,
        "win_pct": percent(wins, len(matches)),
        "surfaces": surfaces,
        "seasons": sorted(seasons.items(), reverse=True)[:8],
        "titles": titles,
        "slam_titles": sum(match.level == "G" for match in titles),
        "head_to_head": head_to_head,
        "aces_per_match": round(aces / ace_matches, 1) if ace_matches else None,
        "first_serve_pct": percent(first_in, serve_points),
        "first_serve_won_pct": percent(first_won, first_in),
        "break_saved_pct": percent(break_saved, break_faced),
        "recent_rank": recent_rank,
    }


def community_statistics(match):
    reviews = db.session.scalars(
        select(Review).where(Review.match_id == match.id, Review.is_public.is_(True))
    ).all()
    ratings = [review.rating_half for review in reviews if review.rating_half is not None]
    return {
        "average": round(sum(ratings) / len(ratings), 1) if ratings else None,
        "count": len(ratings),
        "logs": len(reviews),
        "distribution": {score: sum(rating == score for rating in ratings) for score in range(10, 0, -1)},
    }


def diary_statistics(reviews):
    reviews = list(reviews)
    ratings = [review.rating_half for review in reviews if review.rating_half is not None]
    surfaces = Counter(review.match.surface for review in reviews)
    tours = Counter(review.match.tour for review in reviews)
    players = Counter()
    for review in reviews:
        players[review.match.winner.name] += 1
        players[review.match.loser.name] += 1
    return {
        "logs": len(reviews),
        "favorites": sum(review.is_favorite for review in reviews),
        "average": round(sum(ratings) / len(ratings), 1) if ratings else None,
        "average_ten": round(sum(ratings) / len(ratings), 1) if ratings else None,
        "surfaces": surfaces.most_common(),
        "tours": tours.most_common(),
        "players": players.most_common(5),
        "years": Counter(review.watched_on.year for review in reviews).most_common(),
        "rating_bands": [(score, sum(rating == score for rating in ratings)) for score in range(10, 0, -1)],
        "rating_scale": [(score, sum(review.rating_half == score for review in reviews)) for score in range(1, 11)],
        "rated_count": len(ratings),
    }
