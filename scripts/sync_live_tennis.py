"""Scheduled live score import. Run inside an application context."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tennisd import create_app, db
from tennisd.live_tennis import sync_matches
from tennisd.models import IngestionCursor


PROVIDER = "livetennisapi"


def feed_due(feed, now, interval):
    cursor = db.session.get(IngestionCursor, (PROVIDER, feed))
    if cursor is None:
        return True
    try:
        previous = datetime.fromisoformat(cursor.value)
    except (TypeError, ValueError):
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    return now - previous >= interval


def mark_feed(feed, now):
    cursor = db.session.get(IngestionCursor, (PROVIDER, feed))
    if cursor is None:
        cursor = IngestionCursor(provider=PROVIDER, feed=feed, value=now.isoformat())
        db.session.add(cursor)
    else:
        cursor.value = now.isoformat()
    db.session.commit()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    app = create_app({"AUTO_CREATE_DB": False})
    with app.app_context():
        now = datetime.now(timezone.utc)
        modes = [mode]
        if mode == "auto":
            modes = []
            if feed_due("live", now, timedelta(minutes=14)):
                modes.append("live")
            if feed_due("upcoming", now, timedelta(hours=6)):
                modes.append("upcoming")
        if not modes:
            print("Live Tennis API refresh is not due yet.")
            return
        for selected in modes:
            count = sync_matches(selected, now=now)
            mark_feed(selected, now)
            print(f"Synced {count} {selected} ATP, WTA, Challenger and ITF matches.")


if __name__ == "__main__":
    main()
