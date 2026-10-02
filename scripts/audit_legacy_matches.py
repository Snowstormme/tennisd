"""Audit or remove the discontinued Jeff Sackmann match catalog."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, or_, select

from tennisd import create_app, db
from tennisd.models import Comment, Match, MatchParticipant, MatchSet, MatchStatistic, Report, Review, WatchlistItem


def legacy_filter():
    # The discontinued importer always generated these two ID prefixes. New
    # Live Tennis API results use the distinct ``lt-`` namespace.
    return (or_(Match.id.like("atp-%"), Match.id.like("wta-%")),)


def counts():
    match_ids = select(Match.id).where(*legacy_filter())
    review_ids = select(Review.id).where(Review.match_id.in_(match_ids))
    comment_ids = select(Comment.id).where(Comment.review_id.in_(review_ids))
    return {
        "matches": db.session.scalar(select(func.count()).select_from(Match).where(*legacy_filter())),
        "reviews": db.session.scalar(select(func.count()).select_from(Review).where(Review.match_id.in_(match_ids))),
        "comments": db.session.scalar(select(func.count()).select_from(Comment).where(Comment.review_id.in_(review_ids))),
        "watchlist_items": db.session.scalar(select(func.count()).select_from(WatchlistItem).where(WatchlistItem.match_id.in_(match_ids))),
        "reports": db.session.scalar(select(func.count()).select_from(Report).where(or_(Report.review_id.in_(review_ids), Report.comment_id.in_(comment_ids)))),
    }


def purge():
    match_ids = select(Match.id).where(*legacy_filter())
    review_ids = select(Review.id).where(Review.match_id.in_(match_ids))
    comment_ids = select(Comment.id).where(Comment.review_id.in_(review_ids))
    db.session.execute(delete(Report).where(or_(Report.review_id.in_(review_ids), Report.comment_id.in_(comment_ids))))
    db.session.execute(delete(Comment).where(Comment.review_id.in_(review_ids)))
    db.session.execute(delete(Review).where(Review.match_id.in_(match_ids)))
    db.session.execute(delete(WatchlistItem).where(WatchlistItem.match_id.in_(match_ids)))
    db.session.execute(delete(MatchStatistic).where(MatchStatistic.match_id.in_(match_ids)))
    db.session.execute(delete(MatchParticipant).where(MatchParticipant.match_id.in_(match_ids)))
    db.session.execute(delete(MatchSet).where(MatchSet.match_id.in_(match_ids)))
    db.session.execute(delete(Match).where(*legacy_filter()))
    db.session.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--delete-community-data", action="store_true")
    args = parser.parse_args()
    app = create_app({"AUTO_CREATE_DB": False})
    with app.app_context():
        before = counts()
        print("Legacy catalog impact:", before)
        if args.apply:
            if not args.delete_community_data and any(before[key] for key in ("reviews", "comments", "watchlist_items", "reports")):
                raise SystemExit("Refusing to remove user data without --delete-community-data.")
            purge()
            print("Legacy catalog removed. Remaining impact:", counts())
