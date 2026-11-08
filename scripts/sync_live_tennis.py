"""Scheduled live score import. Run inside an application context."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tennisd import create_app, db
from tennisd.live_tennis import sync_matches
from tennisd.models import IngestionCursor, IngestionRun, LiveMatch, Player, FollowedPlayer, TournamentSubscription
from tennisd.notifications import create_notification
from tennisd.identities import profiles_for_matches
from tennisd.snapshot_policy import current_snapshot_condition
from sqlalchemy import select


PROVIDER = "livetennisapi"


def feed_due(feed, now):
    """Refresh once per UTC calendar day, regardless of scheduler jitter."""
    cursor = db.session.get(IngestionCursor, (PROVIDER, feed))
    if cursor is None:
        return True
    try:
        previous = datetime.fromisoformat(cursor.value)
    except (TypeError, ValueError):
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return previous.astimezone(timezone.utc).date() < now.astimezone(timezone.utc).date()


def mark_feed(feed, now):
    cursor = db.session.get(IngestionCursor, (PROVIDER, feed))
    if cursor is None:
        cursor = IngestionCursor(provider=PROVIDER, feed=feed, value=now.isoformat())
        db.session.add(cursor)
    else:
        cursor.value = now.isoformat()
    db.session.commit()


def notify_followers(now):
    """Create deduplicated alerts for followed players and tournaments."""
    matches = db.session.scalars(select(LiveMatch).where(
        LiveMatch.status.in_(("live", "upcoming")),
        LiveMatch.synced_at >= now - timedelta(minutes=10),
        current_snapshot_condition(now),
    )).all()
    if not matches:
        return 0
    followed = db.session.execute(
        select(FollowedPlayer.user_id, FollowedPlayer.player_id, Player.name, Player.tour)
        .join(Player, Player.id == FollowedPlayer.player_id)
    ).all()
    subscriptions = db.session.execute(
        select(TournamentSubscription.user_id, TournamentSubscription.tour, TournamentSubscription.tournament)
    ).all()
    profiles = profiles_for_matches(matches)
    created = 0
    for match in matches:
        url = f"/live-matches/{quote(match.provider_id, safe='')}"
        title = "Daily match snapshot" if match.status == "live" else "Upcoming match"
        body = f"{match.player1_name} vs {match.player2_name} · {match.tournament}"
        participant_ids = {player.id for player in profiles[match.provider_id] if player is not None}
        for user_id, player_id, _player_name, _player_tour in followed:
            if player_id in participant_ids and create_notification(
                user_id, kind="followed_player_match", title=title, body=body, url=url,
            ):
                created += 1
        for user_id, tour, tournament in subscriptions:
            if tour == match.tour and tournament.casefold() == match.tournament.casefold() and create_notification(
                user_id, kind="followed_tournament_match", title=title, body=body, url=url,
            ):
                created += 1
    db.session.commit()
    return created


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    app = create_app({"AUTO_CREATE_DB": False})
    with app.app_context():
        now = datetime.now(timezone.utc)
        modes = [mode]
        if mode == "auto":
            modes = []
            if feed_due("live", now):
                modes.append("live")
            if feed_due("upcoming", now):
                modes.append("upcoming")
        if not modes:
            print("Live Tennis API refresh is not due yet.")
            return
        for selected in modes:
            run = IngestionRun(provider=f"{PROVIDER}:{selected}", started_at=now)
            db.session.add(run)
            db.session.commit()
            try:
                count = sync_matches(selected, now=now)
            except Exception as error:
                db.session.rollback()
                failed_run = db.session.get(IngestionRun, run.id)
                failed_run.status = "failed"
                failed_run.finished_at = datetime.now(timezone.utc)
                # Keep provider responses and connection details out of the
                # database; the workflow log remains the diagnostic surface.
                failed_run.error = type(error).__name__
                db.session.commit()
                raise
            run.status = "success"
            run.finished_at = datetime.now(timezone.utc)
            run.records_seen = count
            run.records_written = count
            db.session.commit()
            mark_feed(selected, now)
            notified = notify_followers(now)
            print(f"Synced {count} {selected} ATP, WTA, Challenger and ITF matches; queued {notified} notifications.")


if __name__ == "__main__":
    main()
