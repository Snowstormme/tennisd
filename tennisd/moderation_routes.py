"""Feedback, polls and administrator moderation routes."""

import hmac
import secrets

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_, select

from . import db
from .models import Comment, FeedbackSubmission, Poll, PollOption, PollVote, Report, Review, utcnow
from .security import client_ip, limit_action
from .web import schema_table_exists


def register_moderation_routes(site):
    """Register feedback and moderation routes on the shared site blueprint."""

    def require_moderator():
        state = current_user.auth_state if current_user.is_authenticated else None
        if (
            not current_user.is_authenticated
            or current_user.email != current_app.config["ADMIN_EMAIL"]
            or not state or not state.email_verified_at
        ):
            abort(403)

    def feedback_tables_ready():
        return all(schema_table_exists(name) for name in (
            "feedback_submission", "poll", "poll_option", "poll_vote",
        ))

    def feedback_visitor_key():
        token = session.get("feedback_visitor")
        if not token:
            token = secrets.token_urlsafe(24)
            session["feedback_visitor"] = token
        identity = f"user:{current_user.id}" if current_user.is_authenticated else f"guest:{token}"
        return hmac.new(current_app.secret_key.encode(), identity.encode(), "sha256").hexdigest()

    @site.route("/feedback", methods=["GET", "POST"])
    def feedback():
        ready = feedback_tables_ready()
        if request.method == "POST":
            if not ready:
                abort(503)
            category = request.form.get("category", "").strip().lower()
            message = request.form.get("message", "").strip()
            raw_rating = request.form.get("rating", "").strip()
            page_path = request.form.get("page_path", "").strip()
            rating = int(raw_rating) if raw_rating.isdigit() else None
            if category not in {"idea", "bug", "content", "other"}:
                flash("Choose a feedback category.", "error")
            elif rating is not None and rating not in range(1, 6):
                flash("Choose a rating from 1 to 5.", "error")
            elif not 10 <= len(message) <= 2000:
                flash("Write between 10 and 2,000 characters.", "error")
            else:
                limit_action("feedback", current_user.id if current_user.is_authenticated else client_ip(), 5, 3600)
                if (
                    not page_path.startswith("/") or page_path.startswith("//")
                    or "\\" in page_path or len(page_path) > 300
                ):
                    page_path = None
                db.session.add(FeedbackSubmission(
                    user_id=current_user.id if current_user.is_authenticated else None,
                    category=category, rating=rating, message=message, page_path=page_path,
                ))
                db.session.commit()
                flash("Thank you — your feedback is now in the Tennisd roadmap inbox.", "success")
                return redirect(url_for("site.feedback"))

        poll = None
        selected_option_id = None
        if ready:
            now = utcnow()
            poll = db.session.scalar(select(Poll).where(
                Poll.is_active.is_(True), Poll.starts_at <= now,
                or_(Poll.ends_at.is_(None), Poll.ends_at > now),
            ).order_by(Poll.created_at.desc()))
            if poll:
                vote = db.session.scalar(select(PollVote).where(
                    PollVote.poll_id == poll.id,
                    PollVote.visitor_key == feedback_visitor_key(),
                ))
                selected_option_id = vote.option_id if vote else None
        return render_template("feedback.html", ready=ready, poll=poll, selected_option_id=selected_option_id)

    @site.post("/feedback/polls/<int:poll_id>/vote")
    def vote_poll(poll_id):
        if not feedback_tables_ready():
            abort(503)
        now = utcnow()
        poll = db.session.scalar(select(Poll).where(
            Poll.id == poll_id, Poll.is_active.is_(True), Poll.starts_at <= now,
            or_(Poll.ends_at.is_(None), Poll.ends_at > now),
        ))
        if not poll:
            abort(404)
        raw_option = request.form.get("option", "")
        option = db.session.get(PollOption, int(raw_option)) if raw_option.isdigit() else None
        if not option or option.poll_id != poll.id:
            flash("Choose one answer.", "error")
            return redirect(url_for("site.feedback"))
        visitor_key = feedback_visitor_key()
        limit_action("poll-vote", visitor_key, 10, 3600)
        vote = db.session.scalar(select(PollVote).where(
            PollVote.poll_id == poll.id, PollVote.visitor_key == visitor_key,
        ))
        if vote:
            vote.option_id = option.id
        else:
            db.session.add(PollVote(
                poll_id=poll.id, option_id=option.id,
                user_id=current_user.id if current_user.is_authenticated else None,
                visitor_key=visitor_key,
            ))
        db.session.commit()
        flash("Vote saved. Thanks for helping shape Tennisd.", "success")
        return redirect(url_for("site.feedback"))

    @site.get("/moderation")
    @login_required
    def moderation():
        require_moderator()
        reports = db.session.scalars(
            select(Report).where(Report.status == "open")
            .order_by(Report.created_at.asc()).limit(100)
        ).all()
        feedback_items = []
        feedback_summary = {"total": 0, "average": None, "categories": []}
        polls = []
        if feedback_tables_ready():
            feedback_items = db.session.scalars(
                select(FeedbackSubmission).order_by(FeedbackSubmission.created_at.desc()).limit(100)
            ).all()
            total, average = db.session.execute(
                select(func.count(FeedbackSubmission.id), func.avg(FeedbackSubmission.rating))
            ).one()
            categories = db.session.execute(
                select(FeedbackSubmission.category, func.count(FeedbackSubmission.id))
                .group_by(FeedbackSubmission.category).order_by(func.count(FeedbackSubmission.id).desc())
            ).all()
            feedback_summary = {"total": total, "average": average, "categories": categories}
            polls = db.session.scalars(select(Poll).order_by(Poll.created_at.desc())).all()
        return render_template(
            "moderation.html", reports=reports, feedback_items=feedback_items,
            feedback_summary=feedback_summary, polls=polls,
        )

    @site.post("/moderation/feedback/<int:feedback_id>/<status>")
    @login_required
    def moderate_feedback(feedback_id, status):
        require_moderator()
        if status not in {"new", "reviewed", "planned", "closed"}:
            abort(400)
        item = db.get_or_404(FeedbackSubmission, feedback_id)
        item.status = status
        db.session.commit()
        return redirect(url_for("site.moderation") + "#feedback")

    @site.post("/moderation/reports/<int:report_id>/<action>")
    @login_required
    def moderate_report(report_id, action):
        require_moderator()
        report = db.get_or_404(Report, report_id)
        if action == "dismiss":
            report.status = "dismissed"
        elif action == "remove":
            target = report.review or report.comment
            db.session.delete(target)
        else:
            abort(400)
        db.session.commit()
        return redirect(url_for("site.moderation"))
