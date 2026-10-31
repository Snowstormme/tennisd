"""Profile, account settings and personal data routes."""

import csv
import io

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for, Response
from flask_login import current_user, login_required, logout_user
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import joinedload

from . import db
from .models import (
    AuthState, Comment, Friendship, FollowedPlayer, Match, ProfileImage,
    Review, TournamentSubscription, User, WatchlistItem, utcnow,
)
from .security import limit_action
from .stats import diary_statistics
from .web import schema_table_exists


def register_profile_routes(
    site, *, match_location, tournament_slug, prepare_profile_image, sign_in_user,
):
    """Register profile and account routes on the shared site blueprint."""

    @site.get("/me")
    @login_required
    def my_profile():
        return redirect(url_for("site.profile", username=current_user.username))

    @site.get("/u/<username>")
    def profile(username):
        user = db.session.scalar(select(User).where(User.username == username.lower()))
        if user is None:
            abort(404)
        own = current_user.is_authenticated and current_user.id == user.id
        available_tabs = {"profile", "diary", "likes"}
        if own:
            available_tabs.update({"watchlist", "friends"})
        active_tab = request.args.get("tab", "profile").lower()
        if active_tab not in available_tabs:
            active_tab = "profile"
        statement = select(Review).where(Review.user_id == user.id)
        if not own:
            statement = statement.where(Review.is_public.is_(True))
        reviews = db.session.scalars(
            statement.options(
                joinedload(Review.match).joinedload(Match.winner),
                joinedload(Review.match).joinedload(Match.loser),
            ).order_by(Review.watched_on.desc(), Review.id.desc())
        ).all()
        followed = []
        tournament_subscriptions = []
        watchlist = []
        friends = []
        friend_state = None
        if own:
            followed = db.session.scalars(
                select(FollowedPlayer).where(FollowedPlayer.user_id == user.id)
                .options(joinedload(FollowedPlayer.player)).limit(12)
            ).all()
            watchlist = db.session.scalars(
                select(WatchlistItem).where(WatchlistItem.user_id == user.id)
                .options(joinedload(WatchlistItem.match))
                .order_by(WatchlistItem.added_at.desc())
            ).all()
            if schema_table_exists(TournamentSubscription.__tablename__):
                tournament_subscriptions = db.session.scalars(
                    select(TournamentSubscription).where(TournamentSubscription.user_id == user.id)
                    .order_by(TournamentSubscription.created_at.desc())
                ).all()
            connections = db.session.scalars(select(Friendship).where(
                or_(Friendship.requester_id == user.id, Friendship.addressee_id == user.id),
                Friendship.status == "accepted",
            ).options(joinedload(Friendship.requester), joinedload(Friendship.addressee))).all()
            friends = [item.addressee if item.requester_id == user.id else item.requester for item in connections]
        elif current_user.is_authenticated:
            friend_state = db.session.scalar(select(Friendship.status).where(or_(
                (Friendship.requester_id == current_user.id) & (Friendship.addressee_id == user.id),
                (Friendship.requester_id == user.id) & (Friendship.addressee_id == current_user.id),
            )))
        favorites = [review for review in reviews if review.is_favorite]
        return render_template(
            "profile.html", user=user, reviews=reviews,
            stats=diary_statistics(reviews), own=own, followed=followed, watchlist=watchlist,
            favorites=favorites, active_tab=active_tab, friends=friends, friend_state=friend_state,
            tournament_subscriptions=tournament_subscriptions, tournament_slug=tournament_slug,
            match_location=match_location,
        )

    @site.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings():
        if request.method == "POST":
            display_name = request.form.get("display_name", "").strip()
            bio = request.form.get("bio", "").strip()
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            avatar_upload = request.files.get("avatar")
            avatar_data = None
            avatar_error = None
            if avatar_upload and avatar_upload.filename:
                limit_action("profile-image", str(current_user.id), 10, 3600)
                try:
                    avatar_data = prepare_profile_image(avatar_upload)
                except ValueError as error:
                    avatar_error = str(error)
            if new_password:
                limit_action("password-change", str(current_user.id), 5, 3600)
            if avatar_error:
                flash(avatar_error, "error")
            elif not 1 <= len(display_name) <= 60 or len(bio) > 280:
                flash("Display name or bio is too long.", "error")
            elif new_password and (
                not current_user.check_password(current_password) or not 10 <= len(new_password) <= 128
            ):
                flash("Check your current password; the new one needs 10–128 characters.", "error")
            else:
                current_user.display_name = display_name
                current_user.bio = bio
                if avatar_data is not None:
                    if current_user.profile_image is None:
                        current_user.profile_image = ProfileImage(mime_type="image/webp", image_data=avatar_data)
                    else:
                        current_user.profile_image.mime_type = "image/webp"
                        current_user.profile_image.image_data = avatar_data
                        current_user.profile_image.updated_at = utcnow()
                elif request.form.get("remove_avatar") == "on":
                    current_user.profile_image = None
                if new_password:
                    current_user.set_password(new_password)
                    if current_user.auth_state is None:
                        current_user.auth_state = AuthState(session_version=0)
                    current_user.auth_state.session_version += 1
                db.session.commit()
                if new_password:
                    sign_in_user(current_user)
                flash("Settings saved.", "success")
                return redirect(url_for("site.settings"))
        return render_template("settings.html")

    @site.get("/settings/export")
    @login_required
    def export_diary():
        reviews = db.session.scalars(
            select(Review).where(Review.user_id == current_user.id)
            .options(joinedload(Review.match)).order_by(Review.watched_on)
        ).all()
        follows = db.session.scalars(
            select(FollowedPlayer).where(FollowedPlayer.user_id == current_user.id)
            .options(joinedload(FollowedPlayer.player))
        ).all()
        watchlist = db.session.scalars(
            select(WatchlistItem).where(WatchlistItem.user_id == current_user.id)
        ).all()
        tournament_subscriptions = []
        if schema_table_exists(TournamentSubscription.__tablename__):
            tournament_subscriptions = db.session.scalars(
                select(TournamentSubscription).where(TournamentSubscription.user_id == current_user.id)
                .order_by(TournamentSubscription.created_at)
            ).all()
        comments = db.session.scalars(
            select(Comment).where(Comment.user_id == current_user.id)
            .options(joinedload(Comment.review))
            .order_by(Comment.created_at)
        ).all()
        payload = {
            "schema_version": 1,
            "username": current_user.username,
            "email": current_user.email,
            "display_name": current_user.display_name,
            "bio": current_user.bio,
            "has_profile_photo": current_user.profile_image is not None,
            "joined_at": current_user.created_at.isoformat(),
            "diary": [
                {
                    "match_id": review.match_id,
                    "match": f"{review.match.winner.name} vs {review.match.loser.name}",
                    "tournament": review.match.tournament,
                    "watched_on": review.watched_on.isoformat(),
                    "rating_out_of_ten": review.rating_half,
                    "review": review.body,
                    "favorite": review.is_favorite,
                    "public": review.is_public,
                    "spoilers": review.has_spoilers,
                }
                for review in reviews
            ],
            "followed_players": [item.player.name for item in follows],
            "followed_tournaments": [
                {"tour": item.tour, "tournament": item.tournament}
                for item in tournament_subscriptions
            ],
            "watchlist_match_ids": [item.match_id for item in watchlist],
            "comments": [
                {
                    "match_id": comment.review.match_id,
                    "review_id": comment.review_id,
                    "body": comment.body,
                    "created_at": comment.created_at.isoformat(),
                }
                for comment in comments
            ],
        }
        response = jsonify(payload)
        response.headers["Content-Disposition"] = f'attachment; filename="tennisd-{current_user.username}.json"'
        response.cache_control.no_store = True
        return response

    @site.get("/settings/export.csv")
    @login_required
    def export_diary_csv():
        """Export the same account data as a spreadsheet-friendly CSV file."""
        payload = export_diary().get_json()
        columns = (
            "record_type", "schema_version", "username", "email", "display_name", "bio",
            "has_profile_photo", "joined_at", "match_id", "match", "tournament", "watched_on",
            "rating_out_of_ten", "review", "favorite", "public", "spoilers", "player", "tour",
            "followed_tournament", "comment_body", "review_id", "created_at",
        )
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()

        def safe_cell(value):
            text = "" if value is None else str(value)
            return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text

        def write_row(values):
            writer.writerow({key: safe_cell(value) for key, value in values.items()})

        write_row({
            "record_type": "account", "schema_version": payload["schema_version"],
            "username": payload["username"], "email": payload["email"],
            "display_name": payload["display_name"], "bio": payload["bio"],
            "has_profile_photo": payload["has_profile_photo"], "joined_at": payload["joined_at"],
        })
        for item in payload["diary"]:
            write_row({"record_type": "diary", **item})
        for player in payload["followed_players"]:
            write_row({"record_type": "followed_player", "player": player})
        for item in payload["followed_tournaments"]:
            write_row({
                "record_type": "followed_tournament", "tour": item["tour"],
                "followed_tournament": item["tournament"],
            })
        for match_id in payload["watchlist_match_ids"]:
            write_row({"record_type": "watchlist", "match_id": match_id})
        for item in payload["comments"]:
            write_row({
                "record_type": "comment", "match_id": item["match_id"],
                "review_id": item["review_id"], "comment_body": item["body"],
                "created_at": item["created_at"],
            })

        response = Response(output.getvalue(), content_type="text/csv; charset=utf-8")
        response.headers["Content-Disposition"] = (
            f'attachment; filename="tennisd-{current_user.username}.csv"'
        )
        response.cache_control.no_store = True
        return response

    @site.post("/settings/delete-account")
    @login_required
    def delete_account():
        limit_action("account-delete", str(current_user.id), 5, 3600)
        if (
            request.form.get("username", "").strip().lower() != current_user.username
            or not current_user.check_password(request.form.get("password", ""))
        ):
            flash("Username or password was incorrect. Your account was not deleted.", "error")
            return redirect(url_for("site.settings"))
        user = db.session.get(User, current_user.id)
        db.session.execute(delete(Friendship).where(or_(
            Friendship.requester_id == user.id, Friendship.addressee_id == user.id,
        )))
        db.session.execute(delete(Comment).where(Comment.user_id == user.id))
        db.session.execute(delete(FollowedPlayer).where(FollowedPlayer.user_id == user.id))
        if schema_table_exists(TournamentSubscription.__tablename__):
            db.session.execute(delete(TournamentSubscription).where(TournamentSubscription.user_id == user.id))
        db.session.execute(delete(WatchlistItem).where(WatchlistItem.user_id == user.id))
        db.session.delete(user)
        db.session.commit()
        session.clear()
        logout_user()
        flash("Your account and diary have been deleted.", "success")
        return redirect(url_for("site.home"))
