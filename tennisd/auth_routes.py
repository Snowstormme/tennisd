"""Account registration, authentication and password recovery routes."""

import re

import requests
from flask import abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, logout_user
from sqlalchemy import delete, or_, select

from . import db
from .models import AuthState, AuthToken, User, utcnow
from .security import client_ip, limit_action, send_account_email, valid_token, valid_verification_code
from .web import safe_next


def register_auth_routes(site, *, sign_in_user):
    """Register account routes on the shared site blueprint."""

    @site.route("/register", methods=["GET", "POST"])
    def register():
        if not current_app.config["REGISTRATION_ENABLED"]:
            abort(503)
        if current_user.is_authenticated:
            return redirect(url_for("site.my_profile"))
        if request.method == "POST":
            limit_action("register-ip", client_ip(), 5, 3600)
            username = request.form.get("username", "").strip().lower()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            if not re.fullmatch(r"[a-z0-9_]{3,24}", username):
                flash("Username: 3–24 lowercase letters, numbers or underscores.", "error")
            elif not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 255:
                flash("Enter a valid email address.", "error")
            elif len(password) < 10 or len(password) > 128:
                flash("Use a password with 10–128 characters.", "error")
            elif db.session.scalar(select(User.id).where(or_(User.username == username, User.email == email))):
                flash("That username or email is already in use.", "error")
            else:
                user = User(username=username, email=email, display_name=username)
                user.set_password(password)
                user.auth_state = AuthState(
                    email_verified_at=None if current_app.config["REQUIRE_EMAIL_VERIFICATION"] else utcnow()
                )
                db.session.add(user)
                db.session.commit()
                if current_app.config["REQUIRE_EMAIL_VERIFICATION"]:
                    try:
                        send_account_email(user, "verify")
                        session["verification_email"] = user.email
                        flash("We sent a four-digit verification code to your email.", "success")
                    except requests.RequestException:
                        current_app.logger.exception("Verification email delivery failed")
                        flash("We could not send your email. Use the resend link shortly.", "error")
                    return redirect(url_for("site.check_email"))
                sign_in_user(user)
                flash("Welcome to Tennisd. Your diary is ready.", "success")
                return redirect(url_for("site.my_profile"))
        return render_template("auth.html", mode="register")

    @site.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("site.my_profile"))
        if request.method == "POST":
            identity = request.form.get("identity", "").strip().lower()
            password = request.form.get("password", "")
            limit_action("login-ip", client_ip(), 20, 900)
            limit_action("login-identity", identity[:255], 8, 900)
            user = db.session.scalar(
                select(User).where(or_(User.username == identity, User.email == identity))
            )
            if user and len(password) <= 128 and user.check_password(password):
                if current_app.config["REQUIRE_EMAIL_VERIFICATION"] and (
                    user.auth_state is None or user.auth_state.email_verified_at is None
                ):
                    session["verification_email"] = user.email
                    flash("Verify your email before logging in.", "error")
                    return redirect(url_for("site.check_email"))
                if not user.password_hash.startswith("$argon2id$"):
                    user.set_password(password)
                    db.session.commit()
                sign_in_user(user)
                return redirect(safe_next("site.my_profile"))
            flash("Incorrect username, email or password.", "error")
        return render_template("auth.html", mode="login")

    @site.route("/check-email", methods=["GET", "POST"])
    def check_email():
        email = session.get("verification_email", "")
        if request.method == "POST":
            limit_action("verify-code-ip", client_ip(), 20, 900)
            email = request.form.get("email", "").strip().lower()[:255]
            code = request.form.get("code", "").strip()
            limit_action("verify-code-email", email, 10, 900)
            user = db.session.scalar(select(User).where(User.email == email))
            saved = valid_verification_code(user, code)
            if saved is None or user.auth_state is None or user.auth_state.email_verified_at is not None:
                flash("The code is incorrect or has expired.", "error")
            else:
                user.auth_state.email_verified_at = utcnow()
                db.session.delete(saved)
                db.session.commit()
                session.pop("verification_email", None)
                flash("Email verified. You can log in now.", "success")
                return redirect(url_for("site.login"))
        return render_template("auth_action.html", mode="check", verification_email=email)

    @site.route("/resend-verification", methods=["GET", "POST"])
    def resend_verification():
        if not (current_app.config["BREVO_API_KEY"] or current_app.config.get("MAIL_DELIVERY")):
            abort(503)
        if request.method == "POST":
            limit_action("resend-ip", client_ip(), 5, 3600)
            email = request.form.get("email", "").strip().lower()[:255]
            limit_action("resend-email", email, 3, 3600)
            user = db.session.scalar(select(User).where(User.email == email))
            if user and (not user.auth_state or not user.auth_state.email_verified_at):
                try:
                    send_account_email(user, "verify")
                    session["verification_email"] = user.email
                except requests.RequestException:
                    current_app.logger.exception("Verification email delivery failed")
            flash("If this address needs verification, a new code has been sent.", "success")
            return redirect(url_for("site.check_email"))
        return render_template("auth_action.html", mode="resend")

    @site.route("/verify-email/<token>", methods=["GET", "POST"])
    def verify_email(token):
        saved = valid_token(token, "verify")
        if saved is None:
            flash("This verification link has expired. Request a new one.", "error")
            return redirect(url_for("site.resend_verification"))
        if request.method == "POST":
            user = saved.user
            if user.auth_state is None:
                user.auth_state = AuthState(session_version=0)
            user.auth_state.email_verified_at = utcnow()
            db.session.delete(saved)
            db.session.commit()
            flash("Email verified. You can log in now.", "success")
            return redirect(url_for("site.login"))
        return render_template("auth_action.html", mode="verify", token=token)

    @site.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if not (current_app.config["BREVO_API_KEY"] or current_app.config.get("MAIL_DELIVERY")):
            abort(503)
        if request.method == "POST":
            limit_action("reset-ip", client_ip(), 5, 3600)
            email = request.form.get("email", "").strip().lower()[:255]
            user = db.session.scalar(select(User).where(User.email == email))
            if user:
                try:
                    send_account_email(user, "reset")
                except requests.RequestException:
                    current_app.logger.exception("Password reset email delivery failed")
            flash("If an account uses this address, a reset link has been sent.", "success")
            return redirect(url_for("site.login"))
        return render_template("auth_action.html", mode="forgot")

    @site.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        saved = valid_token(token, "reset")
        if saved is None:
            flash("This reset link has expired. Request a new one.", "error")
            return redirect(url_for("site.forgot_password"))
        if request.method == "POST":
            limit_action("reset-submit-ip", client_ip(), 10, 3600)
            password = request.form.get("password", "")
            if not 10 <= len(password) <= 128:
                flash("Use a password with 10–128 characters.", "error")
            else:
                user = saved.user
                user.set_password(password)
                if user.auth_state is None:
                    user.auth_state = AuthState(session_version=0)
                user.auth_state.session_version += 1
                db.session.execute(delete(AuthToken).where(AuthToken.user_id == user.id))
                db.session.commit()
                session.clear()
                flash("Password changed. Log in with your new password.", "success")
                return redirect(url_for("site.login"))
        return render_template("auth_action.html", mode="reset", token=token)

    @site.post("/logout")
    @login_required
    def logout():
        session.clear()
        logout_user()
        return redirect(url_for("site.home"))
