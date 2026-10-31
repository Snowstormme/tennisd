"""Notifications, friendships and followed-player actions."""

from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload

from . import db
from .models import Comment, FollowedPlayer, Friendship, Player, Review, User


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
        return render_template("notifications.html", requests_in=requests_in, comments=comments)

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
