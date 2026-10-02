"""Account tokens, email delivery and database-backed request limits."""

import hashlib
import hmac
import re
import secrets
import time

import requests
from flask import abort, current_app, request
from sqlalchemy import delete, func, select

from . import db
from .models import AuthToken, RateLimitEvent


def limit_action(action, subject, maximum, seconds):
    """Count attempts in the shared database so restarts do not reset limits."""
    key = hmac.new(
        current_app.secret_key.encode(), f"{action}:{subject}".encode(), hashlib.sha256
    ).hexdigest()
    now = int(time.time())
    recent = db.session.scalar(
        select(func.count(RateLimitEvent.id)).where(
            RateLimitEvent.key_hash == key,
            RateLimitEvent.created_at > now - seconds,
        )
    )
    if recent >= maximum:
        abort(429)
    db.session.add(RateLimitEvent(key_hash=key, created_at=now))
    if secrets.randbelow(100) == 0:
        db.session.execute(delete(RateLimitEvent).where(RateLimitEvent.created_at < now - 86400))
    db.session.commit()


def client_ip():
    return request.remote_addr or "unknown"


def issue_token(user, purpose, lifetime_seconds):
    raw = secrets.token_urlsafe(32)
    db.session.execute(
        delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.purpose == purpose)
    )
    db.session.add(AuthToken(
        digest=hashlib.sha256(raw.encode()).hexdigest(),
        user_id=user.id,
        purpose=purpose,
        expires_at=int(time.time()) + lifetime_seconds,
    ))
    db.session.commit()
    return raw


def valid_token(raw, purpose):
    if not raw or len(raw) > 128:
        return None
    token = db.session.get(AuthToken, hashlib.sha256(raw.encode()).hexdigest())
    if token and token.purpose == purpose and token.expires_at > int(time.time()):
        return token
    return None


def _verification_digest(user_id, code):
    return hmac.new(
        current_app.secret_key.encode(), f"verify:{user_id}:{code}".encode(), hashlib.sha256
    ).hexdigest()


def issue_verification_code(user):
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.session.execute(
        delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.purpose == "verify")
    )
    db.session.add(AuthToken(
        digest=_verification_digest(user.id, code),
        user_id=user.id,
        purpose="verify",
        expires_at=int(time.time()) + 600,
    ))
    db.session.commit()
    return code


def valid_verification_code(user, code):
    if user is None or not re.fullmatch(r"\d{6}", code or ""):
        return None
    token = db.session.get(AuthToken, _verification_digest(user.id, code))
    if token and token.purpose == "verify" and token.expires_at > int(time.time()):
        return token
    return None


def send_account_email(user, purpose):
    base = current_app.config["PUBLIC_BASE_URL"].rstrip("/")
    if purpose == "verify":
        code = issue_verification_code(user)
        subject = f"{code} is your Tennisd verification code"
        body = (
            f"Your Tennisd verification code is:\n\n{code}\n\n"
            "Enter this code on Tennisd. It expires in 10 minutes. "
            "If you did not create an account, ignore this email."
        )
        html_body = (
            '<div style="max-width:520px;margin:32px auto;padding:32px;'
            'background:#10231f;color:#f7f2e8;font-family:Arial,sans-serif">'
            '<p style="margin:0 0 24px;color:#8ed3b1;font-weight:700;letter-spacing:2px">TENNISD</p>'
            '<h1 style="font-size:24px;margin:0 0 12px">Verify your email</h1>'
            '<p style="color:#c9d5cf">Enter this code on Tennisd. It expires in 10 minutes.</p>'
            f'<p style="font-size:36px;letter-spacing:8px;font-weight:700;margin:28px 0">{code}</p>'
            '<p style="font-size:13px;color:#91a39a">If you did not create an account, you can ignore this email.</p>'
            '</div>'
        )
    else:
        raw = issue_token(user, purpose, 1800)
        subject = "Reset your Tennisd password"
        reset_url = f"{base}/reset-password/{raw}"
        body = f"Reset your Tennisd password:\n\n{reset_url}\n\nThis link expires in 30 minutes. If you did not request it, ignore this email."
        html_body = (
            '<div style="max-width:520px;margin:32px auto;padding:32px;'
            'background:#10231f;color:#f7f2e8;font-family:Arial,sans-serif">'
            '<p style="margin:0 0 24px;color:#8ed3b1;font-weight:700;letter-spacing:2px">TENNISD</p>'
            '<h1 style="font-size:24px;margin:0 0 12px">Reset your password</h1>'
            '<p style="color:#c9d5cf">This secure link expires in 30 minutes.</p>'
            f'<p style="margin:28px 0"><a href="{reset_url}" style="display:inline-block;padding:12px 18px;'
            'background:#8ed3b1;color:#10231f;text-decoration:none;font-weight:700">Reset password</a></p>'
            '<p style="font-size:13px;color:#91a39a">If you did not request this, you can ignore this email.</p>'
            '</div>'
        )

    delivery = current_app.config.get("MAIL_DELIVERY")
    if delivery:
        delivery(user.email, subject, body)
        return
    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {current_app.config['RESEND_API_KEY']}"},
        json={
            "from": current_app.config["MAIL_FROM"],
            "to": [user.email],
            "subject": subject,
            "text": body,
            "html": html_body,
        },
        timeout=10,
    )
    response.raise_for_status()
