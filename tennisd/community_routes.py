"""Diary entries, comments and content reports."""

from datetime import date, datetime, timedelta, timezone

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import delete, or_, select

from . import db
from .models import Comment, Match, Report, Review, WatchlistItem
from .notifications import create_notification
from .identities import match_identity_is_valid
from .security import limit_action


def register_community_routes(site):
    """Register diary and community actions on the shared site blueprint."""

    @site.post("/matches/<path:match_id>/log")
    @login_required
    def log_match(match_id):
        match = db.get_or_404(Match, match_id)
        if not match_identity_is_valid(match):
            flash("This result is awaiting a data review. Your existing diary entry is preserved.", "error")
            return redirect(url_for("site.match_detail", match_id=match_id))
        try:
            watched_on = date.fromisoformat(request.form.get("watched_on", ""))
            if watched_on > date.today() or watched_on < match.week_start:
                raise ValueError
            rating_text = request.form.get("rating", "")
            rating = int(rating_text) if rating_text else None
            if rating is not None and not 1 <= rating <= 10:
                raise ValueError
        except ValueError:
            flash("Choose a valid viewing date and rating.", "error")
            return redirect(url_for("site.match_detail", match_id=match_id))

        body = request.form.get("body", "").strip()
        if len(body) > 3000:
            flash("Keep your review under 3,000 characters.", "error")
            return redirect(url_for("site.match_detail", match_id=match_id))
        review = db.session.scalar(
            select(Review).where(Review.user_id == current_user.id, Review.match_id == match_id)
        )
        if review is None:
            review = Review(user_id=current_user.id, match_id=match_id)
            db.session.add(review)
        review.watched_on = watched_on
        review.rating_half = rating
        review.body = body
        review.is_favorite = request.form.get("favorite") == "on"
        review.is_public = request.form.get("public") == "on"
        review.has_spoilers = request.form.get("spoilers") == "on"
        watchlist_item = db.session.scalar(select(WatchlistItem).where(
            WatchlistItem.user_id == current_user.id, WatchlistItem.match_id == match_id,
        ))
        if watchlist_item:
            db.session.delete(watchlist_item)
        db.session.commit()
        flash("Your match diary entry was saved.", "success")
        return redirect(url_for("site.match_detail", match_id=match_id))

    @site.post("/reviews/<int:review_id>/delete")
    @login_required
    def delete_review(review_id):
        review = db.get_or_404(Review, review_id)
        if review.user_id != current_user.id:
            abort(403)
        match_id = review.match_id
        db.session.delete(review)
        db.session.commit()
        flash("Diary entry deleted.", "success")
        return redirect(url_for("site.match_detail", match_id=match_id))

    @site.post("/reviews/<int:review_id>/comments")
    @login_required
    def add_comment(review_id):
        review = db.get_or_404(Review, review_id)
        if not review.is_public:
            abort(404)
        body = request.form.get("body", "").strip()
        if not 1 <= len(body) <= 500:
            flash("Comments must be 1–500 characters.", "error")
        else:
            recent = db.session.scalar(select(Comment).where(
                Comment.user_id == current_user.id,
                Comment.created_at > datetime.now(timezone.utc) - timedelta(seconds=15),
            ).limit(1))
            if recent:
                flash("Please wait a moment before commenting again.", "error")
            else:
                db.session.add(Comment(review_id=review.id, user_id=current_user.id, body=body))
                if review.user_id != current_user.id:
                    create_notification(
                        review.user_id, kind="review_comment", title="New reply to your review",
                        body=f"{current_user.display_name} commented on your match review.",
                        url=url_for("site.match_detail", match_id=review.match_id) + f"#review-{review.id}",
                    )
                db.session.commit()
                flash("Comment added.", "success")
        return redirect(url_for("site.match_detail", match_id=review.match_id) + f"#review-{review.id}")

    @site.post("/comments/<int:comment_id>/delete")
    @login_required
    def delete_comment(comment_id):
        comment = db.get_or_404(Comment, comment_id)
        if comment.user_id != current_user.id and comment.review.user_id != current_user.id:
            abort(403)
        match_id = comment.review.match_id
        review_id = comment.review_id
        db.session.delete(comment)
        db.session.commit()
        return redirect(url_for("site.match_detail", match_id=match_id) + f"#review-{review_id}")

    @site.post("/reports")
    @login_required
    def report_content():
        limit_action("report-user", str(current_user.id), 10, 86400)
        target = request.form.get("target", "")
        reason = request.form.get("reason", "")
        if reason not in {"spam", "harassment", "hate", "other"}:
            abort(400)
        if target == "review":
            review = db.get_or_404(Review, request.form.get("target_id", type=int))
            if not review.is_public:
                abort(404)
            match_id = review.match_id
            condition = (Report.reporter_id == current_user.id, Report.review_id == review.id)
            report = Report(reporter_id=current_user.id, review_id=review.id, reason=reason)
        elif target == "comment":
            comment = db.get_or_404(Comment, request.form.get("target_id", type=int))
            if not comment.review.is_public:
                abort(404)
            match_id = comment.review.match_id
            condition = (Report.reporter_id == current_user.id, Report.comment_id == comment.id)
            report = Report(reporter_id=current_user.id, comment_id=comment.id, reason=reason)
        else:
            abort(400)
        if db.session.scalar(select(Report.id).where(*condition, Report.status == "open")) is None:
            db.session.add(report)
            db.session.commit()
        flash("Thank you. This report is queued for review.", "success")
        return redirect(url_for("site.match_detail", match_id=match_id))
