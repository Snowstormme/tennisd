"""Feedback, polls and administrator moderation routes."""

import hmac
import secrets

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, select

from . import db
from .models import Comment, FeedbackSubmission, ModerationAction, Poll, PollOption, PollVote, Report, Review, utcnow
from .security import client_ip, limit_action
from .web import database_id, schema_table_exists
from .moderation_queries import moderation_context


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

    def record_action(action, target_type, target_id, details=None):
        db.session.add(ModerationAction(
            moderator_id=current_user.id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=details,
        ))

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
            rating = int(raw_rating) if raw_rating in ('1','2','3','4','5') else None
            if category not in {"idea", "bug", "content", "other"}:
                flash("Choose a feedback category.", "error")
            elif raw_rating and rating is None:
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
            ).order_by(Poll.created_at.desc(), Poll.id.desc()).limit(1))
            if poll:
                vote = db.session.scalar(select(PollVote).where(
                    PollVote.poll_id == poll.id,
                    PollVote.visitor_key == feedback_visitor_key(),
                ))
                selected_option_id = vote.option_id if vote else None
        return render_template("feedback.html", ready=ready, poll=poll, selected_option_id=selected_option_id)

    @site.post("/feedback/polls/<int:poll_id>/vote")
    def vote_poll(poll_id):
        if database_id(poll_id) is None:
            abort(404)
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
        option_id = database_id(raw_option)
        option = db.session.get(PollOption, option_id) if option_id else None
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
        return render_template("moderation.html", **moderation_context(
            ready=feedback_tables_ready(), tab=request.args.get("tab", "reports"),
            state=request.args.get("state", ""), category=request.args.get("category", ""),
            page=request.args.get("page", 1, type=int)))

    @site.post("/moderation/feedback/<int:feedback_id>/<status>")
    @login_required
    def moderate_feedback(feedback_id, status):
        require_moderator()
        if database_id(feedback_id) is None:
            abort(404)
        if status not in {"new", "reviewed", "planned", "closed"}:
            abort(400)
        item = db.get_or_404(FeedbackSubmission, feedback_id)
        item.status = status
        record_action("update_feedback", "feedback", item.id, f"status={status}")
        db.session.commit()
        return redirect(url_for("site.moderation", tab="feedback", _anchor="results"))

    @site.post("/moderation/reports/<int:report_id>/<action>")
    @login_required
    def moderate_report(report_id, action):
        require_moderator()
        if database_id(report_id) is None:
            abort(404)
        report = db.session.scalar(select(Report).where(Report.id == report_id).with_for_update())
        if report is None:
            abort(404)
        if report.status != "open":
            abort(409)
        if action == "dismiss":
            report.status = "dismissed"
            record_action("dismiss_report", "report", report.id)
        elif action == "remove":
            target = report.review or report.comment
            record_action("remove_content", "review" if report.review else "comment", target.id)
            db.session.delete(target)
        else:
            abort(400)
        db.session.commit()
        return redirect(url_for("site.moderation"))
