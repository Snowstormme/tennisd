"""Helpers for creating durable in-app notifications."""

from . import db
from .models import Notification


def create_notification(user_id, *, kind, title, body, url):
    """Queue a notification in the caller's transaction."""
    db.session.add(Notification(
        user_id=user_id, kind=kind, title=title, body=body, url=url,
    ))
