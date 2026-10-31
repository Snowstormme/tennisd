"""Create the application schema and run a minimal PostgreSQL smoke check."""

import os
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from tennisd import create_app, db
from tennisd.migrations import run_migrations
from tennisd.catalog_audit import catalog_report, readonly_catalog_session
from tennisd.models import Match, Player, Review, User
from tennisd.pagination import bounded_page
from tennisd.stats import diary_statistics
from tennisd.result_corrections import definitions, reviewed_correction


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

    response = app.test_client().get("/healthz")
    if response.status_code != 200 or response.get_data(as_text=True) != "ok":
        raise RuntimeError("Health check failed after PostgreSQL initialization.")

    print("PostgreSQL application smoke check passed.")


if __name__ == "__main__":
    main()
