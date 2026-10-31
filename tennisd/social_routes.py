"""Notifications, friendships and followed-player actions."""

from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload

from . import db
from .models import Comment, FollowedPlayer, Friendship, Notification, Player, Review, User, utcnow
from .notifications import create_notification


def register_social_routes(site):
    """Register social routes on the shared site blueprint."""

    @site.get("/notifications")
    @login_required
    def notifications():
        requests_in = db.session.scalars(
            select(Friendship).where(
                Friendship.addressee_id == current_user.id,
                Friendship.status == "pending",
            ).options(joinedload(Friendship.requester))
            .order_by(Friendship.created_at.desc())
        ).all()
        comments = db.session.scalars(
            select(Comment).join(Review).where(
                Review.user_id == current_user.id,
                Comment.user_id != current_user.id,
            ).options(joinedload(Comment.user), joinedload(Comment.review).joinedload(Review.match))
            .order_by(Comment.created_at.desc()).limit(30)
        ).all()
        in_app = db.session.scalars(
            select(Notification).where(Notification.user_id == current_user.id)
            .order_by(Notification.created_at.desc()).limit(50)
        ).all()
        return render_template(
            "notifications.html", requests_in=requests_in, comments=comments, in_app=in_app,
        )

    @site.post("/notifications/<int:notification_id>/read")
    @login_required
    def mark_notification_read(notification_id):
        notification = db.get_or_404(Notification, notification_id)
        if notification.user_id != current_user.id:
            abort(403)
        notification.read_at = utcnow()
        db.session.commit()
        return redirect(notification.url)

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
        return redirect(url_for("site.notifications"))

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
