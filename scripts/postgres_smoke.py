"""Create the application schema and run a minimal PostgreSQL smoke check."""

import os
from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from tennisd import create_app, db
from tennisd.migrations import run_migrations
from tennisd.catalog_audit import catalog_report, match_snapshot, readonly_catalog_session
from tennisd.models import IngestionRun, Match, MatchParticipant, Player, Review, User
from tennisd.pagination import bounded_page
from tennisd.stats import diary_statistics
from tennisd.result_corrections import batches, corrected_snapshot, definitions, reviewed_batch, reviewed_correction


def check_reviewed_batch():
    batch = "fognini-2024-archive"
    keys = batches()[batch]
    matches = {}
    for key in keys:
        correction = definitions()[key]; before = correction["before"]
        for data in (before["winner"], before["loser"], correction["corrected_player"]):
            if db.session.get(Player, data["id"]) is None:
                db.session.add(Player(**data))
        match = Match(id=before["id"], provider=before["provider"], provider_id=before["provider_id"],
            tour=before["tour"], tournament=before["tournament"], status=before["status"],
            week_start=date.fromisoformat(before["date"]), score=before["score"],
            winner_id=before["winner"]["id"], loser_id=before["loser"]["id"],
            surface="Hard", level="M", round="R?")
        matches[key] = match
        db.session.add(match)
        db.session.add_all(MatchParticipant(**row) for row in correction["related_before"]["participants"])
    db.session.commit()
    with readonly_catalog_session():
        preview = reviewed_batch(batch)
        if preview["blocked"] or preview["changed"] or len(preview["results"]) != 7:
            raise RuntimeError("PostgreSQL reviewed batch preview did not remain read-only.")
    original_runs = db.session.scalar(select(func.count(IngestionRun.id)))
    last = keys[-1]
    matches[last].score = "unreviewed score"; db.session.commit()
    try:
        reviewed_batch(batch, operation="apply")
    except ValueError as error:
        if "changed since review" not in str(error):
            raise
    else:
        raise RuntimeError("PostgreSQL batch unexpectedly accepted a changed final result.")
    # Deliberately commit after catching: the savepoint must already undo all
    # earlier matches, participants and journals in this failed batch.
    db.session.commit()
    for key in keys[:-1]:
        if match_snapshot(matches[key]) != definitions()[key]["before"]:
            raise RuntimeError("PostgreSQL left a partially applied reviewed batch.")
    if db.session.scalar(select(func.count(IngestionRun.id))) != original_runs:
        raise RuntimeError("PostgreSQL left journals from a failed batch.")
    matches[last].score = definitions()[last]["before"]["score"]; db.session.commit()
    reviewed_batch(batch, operation="apply"); db.session.rollback()
    for key in keys:
        if match_snapshot(matches[key]) != definitions()[key]["before"]:
            raise RuntimeError("PostgreSQL committed a successful batch before its caller.")
    if reviewed_batch(batch, operation="apply")["changed"] != 7:
        raise RuntimeError("PostgreSQL did not apply the complete reviewed batch.")
    db.session.commit()
    for key in keys:
        correction = definitions()[key]
        if match_snapshot(matches[key]) != corrected_snapshot(correction):
            raise RuntimeError("PostgreSQL repaired the wrong winner/loser side.")
        for row in correction["related_before"]["participants"]:
            expected = row["player_id"]
            if expected == correction["before"][correction["corrected_side"]]["id"]:
                expected = correction["corrected_player"]["id"]
            if db.session.get(MatchParticipant, row["id"]).player_id != expected:
                raise RuntimeError("PostgreSQL did not repair a related participant.")
    if reviewed_batch(batch, operation="apply")["changed"]:
        raise RuntimeError("PostgreSQL reviewed batch was not idempotent.")
    reviewed_batch(batch, operation="rollback"); db.session.commit()
    for key in keys:
        if match_snapshot(matches[key]) != definitions()[key]["before"]:
            raise RuntimeError("PostgreSQL reviewed batch rollback was incomplete.")


def main():
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("DATABASE_URL must point to PostgreSQL for this smoke check.")

    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "postgres-smoke-test-secret",
        "SQLALCHEMY_DATABASE_URI": database_url,
        "AUTO_CREATE_DB": False,
    })
    run_migrations(app)
    with app.app_context():
        db.session.execute(text("SELECT 1"))
        user_tables = db.session.execute(text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'user'"
        )).scalar_one()
        if user_tables != 1:
            raise RuntimeError("Application schema was not created.")
        db.session.rollback()

        with readonly_catalog_session():
            report = catalog_report(1)
            if report["cross_tour_or_self_matches"] != 0:
                raise RuntimeError("The fresh catalog is unexpectedly inconsistent.")
        try:
            with readonly_catalog_session():
                db.session.execute(text("CREATE TABLE public.forbidden_catalog_write (id integer)"))
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) != "25006":
                raise
        else:
            raise RuntimeError("Catalog preview unexpectedly allowed a database write.")

        key = "itf-sharm-2026-030-kirci-yuan"
        original = definitions()[key]["before"]
        winner, loser = Player(**original["winner"]), Player(**original["loser"])
        match = Match(
            id=original["id"], provider=original["provider"], provider_id=original["provider_id"],
            tour=original["tour"], tournament=original["tournament"], status=original["status"],
            week_start=date.fromisoformat(original["date"]), score=original["score"],
            winner=winner, loser=loser, level="I", round="R32", surface="Hard",
        )
        db.session.add_all((winner, loser, match))
        db.session.commit()

        member = User(username="pgdiary", email="pgdiary@example.test", display_name="PG diary", password_hash="unused-test-value")
        db.session.add(Review(user=member, match=match, watched_on=date(2026,10,31),
            rating_half=7, is_favorite=True, is_public=False, body="PRIVATE_POSTGRES_DIARY_NOTE"))
        db.session.commit()
        own = diary_statistics(member.id)
        public = diary_statistics(member.id, public_only=True)
        if own["logs"] != 1 or own["average_ten"] != 7.0 or own["favorites"] != 1 or public["logs"] != 0:
            raise RuntimeError("PostgreSQL diary summaries did not preserve visibility or totals.")
        page = bounded_page(select(Review).where(Review.user_id == member.id), page=10 ** 100)
        if page.page != 1 or len(page.items) != 1:
            raise RuntimeError("PostgreSQL pagination did not bound an oversized offset.")
        if not reviewed_correction(key, operation="apply")["changed"]:
            raise RuntimeError("PostgreSQL correction did not apply under its row lock.")
        db.session.commit()
        if reviewed_correction(key, operation="apply")["changed"]:
            raise RuntimeError("PostgreSQL correction was not idempotent.")
        if not reviewed_correction(key, operation="rollback")["changed"]:
            raise RuntimeError("PostgreSQL correction could not restore the original result.")
        db.session.commit()

        # Reuse a reviewed existing profile without creating an external mapping.
        existing_key = "us-open-2024-machac-fognini"
        definition = definitions()[existing_key]
        original = definition["before"]
        winner, loser = Player(**original["winner"]), Player(**original["loser"])
        target = Player(**definition["corrected_player"])
        historical = Match(
            id=original["id"], provider=original["provider"], provider_id=original["provider_id"],
            tour=original["tour"], tournament=original["tournament"], status=original["status"],
            week_start=date.fromisoformat(original["date"]), score=original["score"],
            winner=winner, loser=loser, level="G", round="R128", surface="Hard",
        )
        db.session.add_all((winner, loser, target, historical))
        db.session.add_all(MatchParticipant(**row) for row in definition["related_before"]["participants"])
        db.session.commit()
        with readonly_catalog_session():
            reviewed_correction(existing_key)
        reviewed_correction(existing_key, operation="apply"); db.session.commit()
        participant_id = definition["related_before"]["participants"][1]["id"]
        participant = db.session.get(MatchParticipant, participant_id)
        if historical.loser_id != target.id or participant.player_id != target.id:
            raise RuntimeError("PostgreSQL correction did not reuse the reviewed existing profile.")
        reviewed_correction(existing_key, operation="rollback"); db.session.commit()
        if historical.loser_id != original["loser"]["id"] or participant.player_id != original["loser"]["id"]:
            raise RuntimeError("PostgreSQL existing-profile correction could not be reversed.")

        check_reviewed_batch()

    response = app.test_client().get("/healthz")
    if response.status_code != 200 or response.get_data(as_text=True) != "ok":
        raise RuntimeError("Health check failed after PostgreSQL initialization.")

    print("PostgreSQL application smoke check passed.")


if __name__ == "__main__":
    main()
