"""Routes for current provider-backed live matches."""

from flask import abort, jsonify, redirect, render_template, url_for
from sqlalchemy import select

from . import db, clean_score_text, snapshot_is_stale
from .models import IngestionRun, LiveMatch, utcnow
from .snapshot_policy import snapshot_counts, snapshot_visibility_reason
from .web import schema_table_exists, shared_cache


def register_live_routes(
    site, *, current_match_rows, current_match_players,
    archived_match_id_for_live, match_location,
):
    """Register live routes on the shared site blueprint."""

    @site.get("/live-matches/<provider_id>")
    def live_match_detail(provider_id):
        if not schema_table_exists(LiveMatch.__tablename__):
            abort(404)
        match = db.get_or_404(LiveMatch, provider_id)
        archived = archived_match_id_for_live(match)
        if archived:
            return redirect(url_for("site.match_detail", match_id=archived))
        players = current_match_players([match]).get(match.provider_id, (None, None))
        return render_template(
            "live_match.html", match=match, players=players,
            match_location=match_location, snapshot_reason=snapshot_visibility_reason(match),
        )

    @site.get("/api/live-matches")
    def live_matches_api():
        now = utcnow()
        matches = current_match_rows("live", now=now)
        response = jsonify({
            "checked_at": now.isoformat(),
            "matches": [{
                "id": match.provider_id, "score": clean_score_text(match.score),
                "status": match.status, "winner_side": None, "server": match.server,
                "synced_at": match.synced_at.isoformat(),
            } for match in matches]
        })
        return shared_cache(
            response, browser_seconds=15, cdn_seconds=30, stale_seconds=30,
        )

    @site.get("/api/sync-status")
    def sync_status():
        """Expose provider freshness without returning operational secrets."""
        if not schema_table_exists(IngestionRun.__tablename__):
            return jsonify({"provider": "livetennisapi", "status": "unavailable", "feeds": {}})
        feeds = {}
        for feed in ("live", "upcoming"):
            run = db.session.scalar(select(IngestionRun)
                .where(IngestionRun.provider == f"livetennisapi:{feed}")
                .order_by(IngestionRun.started_at.desc(), IngestionRun.id.desc()).limit(1))
            if run is None:
                continue
            feeds[feed] = {
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "records_written": run.records_written,
                "has_error": bool(run.error),
                "stale": snapshot_is_stale(run.finished_at),
            }
        overall = (
            "ok" if all(
                feed in feeds and feeds[feed]["status"] == "success" and not feeds[feed]["stale"]
                for feed in ("live", "upcoming")
            )
            else "degraded"
        )
        response = jsonify({"provider": "livetennisapi", "status": overall, "feeds": feeds,
            "snapshots": snapshot_counts() if schema_table_exists(LiveMatch.__tablename__) else {}})
        response.headers["Cache-Control"] = "no-store"
        return response
