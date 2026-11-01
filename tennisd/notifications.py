"""Helpers for creating durable in-app notifications."""

from datetime import timedelta
import re
from urllib.parse import unquote, urlsplit
from flask import url_for

from sqlalchemy import select

from . import db
from .models import Notification, Review, utcnow


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


def safe_notification_url(target):
    """Stored destinations must stay local, including encoded slash/control forms."""
    if not isinstance(target, str) or not target.startswith("/"):
        return None
    decoded = target
    for _ in range(5):
        if decoded.startswith("//") or "\\" in decoded or any(ord(c) < 32 or ord(c) == 127 for c in decoded):
            return None
        try:
            parsed = urlsplit(decoded)
        except ValueError:
            return None
        if parsed.scheme or parsed.netloc:
            return None
        next_value = unquote(decoded)
        if next_value == decoded:
            return target
        decoded = next_value
    return None


def notification_targets(items, user_id):
    """Resolve current/legacy review links in one bounded visibility query."""
    targets, review_links = {}, {}
    for item in items:
        target = safe_notification_url(item.url)
        targets[item.id] = target
        if target is None:
            continue
        legacy = re.fullmatch(r"/matches/[^?#]+#review-([0-9]+)", target)
        direct = re.fullmatch(r"/reviews/([0-9]+)(?:[?#].*)?", target)
        match = legacy or direct
        if match:
            raw = match.group(1)
            if len(raw) > 10 or int(raw) > 2147483647:
                targets[item.id] = None
            else:
                review_links[item.id] = (int(raw), bool(legacy))
    visible = set()
    if review_links:
        visible = set(db.session.scalars(select(Review.id).where(
            Review.id.in_({key for key, _ in review_links.values()}),
            (Review.is_public.is_(True) | (Review.user_id == user_id)))))
    for notification_id, (review_id, legacy) in review_links.items():
        if review_id not in visible:
            targets[notification_id] = None
        elif legacy:
            targets[notification_id] = url_for("site.review_detail", review_id=review_id, _anchor="comments")
    return targets
