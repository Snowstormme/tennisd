"""Notifications, friendships and followed-player actions."""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import joinedload

from . import db
from .models import Comment, FollowedPlayer, Friendship, Notification, Player, Review, User, utcnow
from .notifications import create_notification, notification_targets
from .pagination import bounded_page


def register_social_routes(site):
    """Register social routes on the shared site blueprint."""

    @site.get("/notifications")
    @login_required
    def notifications():
        tab = request.args.get("tab", "updates")
        if tab not in ("updates", "requests", "replies"):
            tab = "updates"
        state = "unread" if request.args.get("state") == "unread" else "all"
        statements = {
            "updates": select(Notification).where(Notification.user_id == current_user.id)
                .order_by(Notification.created_at.desc(), Notification.id.desc()),
            "requests": select(Friendship).where(Friendship.addressee_id == current_user.id,
                Friendship.status == "pending").options(joinedload(Friendship.requester))
                .order_by(Friendship.created_at.desc(), Friendship.id.desc()),
            "replies": select(Comment).join(Review).where(Review.user_id == current_user.id,
                Comment.user_id != current_user.id)
                .options(joinedload(Comment.user), joinedload(Comment.review).joinedload(Review.match))
                .order_by(Comment.created_at.desc(), Comment.id.desc()),
        }
        totals = {key: db.session.scalar(select(func.count()).select_from(value.order_by(None).subquery()))
                  for key, value in statements.items()}
        unread = db.session.scalar(select(func.count(Notification.id)).where(
            Notification.user_id == current_user.id, Notification.read_at.is_(None)))
        statement = statements[tab]
        if tab == "updates" and state == "unread":
            statement = statement.where(Notification.read_at.is_(None))
        pagination = bounded_page(statement, page=request.args.get("page", 1, type=int), per_page=30)
        in_app = pagination.items if tab == "updates" else []
        return render_template("notifications.html", tab=tab, state=state, totals=totals, unread=unread,
            pagination=pagination, in_app=in_app, targets=notification_targets(in_app, current_user.id),
            requests_in=pagination.items if tab == "requests" else [],
            comments=pagination.items if tab == "replies" else [])

    @site.post("/notifications/read-all")
    @login_required
    def mark_all_notifications_read():
        db.session.execute(update(Notification).where(Notification.user_id == current_user.id,
            Notification.read_at.is_(None)).values(read_at=utcnow()))
        db.session.commit()
        flash("Your unread updates were marked as read.", "success")
        return redirect(url_for("site.notifications"))

    @site.post("/notifications/<int:notification_id>/read")
    @login_required
    def mark_notification_read(notification_id):
        if notification_id > 2147483647:
            abort(404)
        notification = db.get_or_404(Notification, notification_id)
        if notification.user_id != current_user.id:
            abort(403)
        target = notification_targets([notification], current_user.id)[notification.id]
        db.session.execute(update(Notification).where(Notification.id == notification.id,
            Notification.user_id == current_user.id, Notification.read_at.is_(None)).values(read_at=utcnow()))
        db.session.commit()
        if target is None:
            flash("This update's link is no longer available.", "error")
        return redirect(target or url_for("site.notifications"))

    @site.post("/u/<username>/friend")
    @login_required
    def request_friend(username):
        other = db.session.scalar(select(User).where(User.username == username.lower()))
        if other is None:
            abort(404)
        if other.id == current_user.id:
            abort(400)
        existing = db.session.scalar(select(Friendship).where(or_(
            (Friendship.requester_id == current_user.id) & (Friendship.addressee_id == other.id),
            (Friendship.requester_id == other.id) & (Friendship.addressee_id == current_user.id),
        )))
        if existing is None:
            db.session.add(Friendship(requester_id=current_user.id, addressee_id=other.id))
            create_notification(
                other.id, kind="friend_request", title="New friend request",
                body=f"{current_user.display_name} sent you a friend request.",
                url=url_for("site.notifications"),
            )
            db.session.commit()
            flash("Friend request sent.", "success")
        return redirect(url_for("site.profile", username=other.username))

    @site.post("/friend-requests/<int:request_id>/<action>")
    @login_required
    def answer_friend_request(request_id, action):
        if request_id > 2147483647:
            abort(404)
        item = db.get_or_404(Friendship, request_id)
        if item.addressee_id != current_user.id or item.status != "pending" or action not in {"accept", "decline"}:
            abort(403)
        if action == "accept":
            item.status = "accepted"
            create_notification(
                item.requester_id, kind="friend_accept", title="Friend request accepted",
                body=f"{current_user.display_name} accepted your friend request.",
                url=url_for("site.notifications"),
            )
            flash("You are now friends.", "success")
        else:
            db.session.delete(item)
        db.session.commit()
        return redirect(url_for("site.notifications", tab="requests", _anchor="results"))

    @site.post("/players/<player_id>/follow")
    @login_required
    def follow_player(player_id):
        db.get_or_404(Player, player_id)
        follow = db.session.scalar(select(FollowedPlayer).where(
            FollowedPlayer.user_id == current_user.id,
            FollowedPlayer.player_id == player_id,
        ))
        if follow:
            db.session.delete(follow)
        else:
            db.session.add(FollowedPlayer(user_id=current_user.id, player_id=player_id))
        db.session.commit()
        return redirect(url_for("site.player_detail", player_id=player_id))
