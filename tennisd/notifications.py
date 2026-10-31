"""Helpers for creating durable in-app notifications."""

from datetime import timedelta

from sqlalchemy import select

from . import db
from .models import Notification, utcnow


def create_notification(user_id, *, kind, title, body, url, dedupe_hours=24):
    """Queue a notification unless the same alert was recently delivered."""
    recent = db.session.scalar(select(Notification.id).where(
        Notification.user_id == user_id,
        Notification.kind == kind,
        Notification.url == url,
        Notification.created_at >= utcnow() - timedelta(hours=dedupe_hours),
    ).limit(1))
    if recent:
        return False
    db.session.add(Notification(
        user_id=user_id, kind=kind, title=title, body=body, url=url,
    ))
    return True
