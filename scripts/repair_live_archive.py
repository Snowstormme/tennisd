"""Remove false live completions and rebuild only verified final results."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select

from tennisd import create_app, db
from tennisd.live_tennis import ensure_live_player_profiles, preserve_finished_match, score_is_aged, score_is_final, winner_from_score
from tennisd.models import LiveMatch, Match, MatchParticipant, MatchSet, MatchStatistic, Review, WatchlistItem


def main():
    live_ids = select(Match.id).where(Match.provider == "livetennisapi")
    reviews = db.session.scalar(select(func.count()).select_from(Review).where(Review.match_id.in_(live_ids)))
    watchlist = db.session.scalar(select(func.count()).select_from(WatchlistItem).where(WatchlistItem.match_id.in_(live_ids)))
    if reviews or watchlist:
        raise RuntimeError(f"Refusing repair: {reviews} reviews and {watchlist} watchlist items use live archive rows.")
    db.session.execute(delete(MatchStatistic).where(MatchStatistic.match_id.in_(live_ids)))
    db.session.execute(delete(MatchParticipant).where(MatchParticipant.match_id.in_(live_ids)))
    db.session.execute(delete(MatchSet).where(MatchSet.match_id.in_(live_ids)))
    removed = db.session.execute(delete(Match).where(Match.provider == "livetennisapi")).rowcount

    now = datetime.now(timezone.utc)
    rows = db.session.scalars(select(LiveMatch).where(LiveMatch.status.in_(("finished", "verifying")))).all()
    ensure_live_player_profiles(rows)
    db.session.flush()
    preserved = 0
    for match in rows:
        winner = winner_from_score(match.score)
        aged = score_is_aged(match.provider_updated_at, now)
        if winner and score_is_final(match) and aged:
            match.status = "finished"
            match.winner_side = winner
            match.outcome = "completed"
            match.finished_at = match.finished_at or now
            preserved += preserve_finished_match(match, match.finished_at) is not None
        else:
            match.status = "verifying"
            match.winner_side = None
            match.outcome = "unverified"
            match.finished_at = None
    db.session.commit()
    print(f"Removed {removed} unverified archive rows; preserved {preserved} verified finals.")


if __name__ == "__main__":
    app = create_app({"AUTO_CREATE_DB": False})
    with app.app_context():
        main()
