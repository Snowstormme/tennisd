"""Bound reply previews in SQL rather than loading every review's collection."""
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload
from . import db
from .models import Comment


def discussion_previews(review_ids):
    if not review_ids:
        return {}, {}
    ranked = select(Comment.id, Comment.review_id,
        func.row_number().over(partition_by=Comment.review_id,
            order_by=(Comment.created_at.desc(), Comment.id.desc())).label("position"),
        func.count().over(partition_by=Comment.review_id).label("total"),
    ).where(Comment.review_id.in_(review_ids)).subquery()
    rows = db.session.execute(select(Comment, ranked.c.total)
        .join(ranked, Comment.id == ranked.c.id).where(ranked.c.position <= 3)
        .options(joinedload(Comment.user))
        .order_by(Comment.review_id, Comment.created_at.desc(), Comment.id.desc())).all()
    previews, totals = {}, {}
    for comment, total in rows:
        previews.setdefault(comment.review_id, []).append(comment)
        totals[comment.review_id] = total
    return previews, totals
