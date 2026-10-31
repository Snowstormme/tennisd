"""Create the application schema and run a minimal PostgreSQL smoke check."""

import os

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tennisd import create_app, db
from tennisd.migrations import run_migrations
from tennisd.catalog_audit import catalog_report, readonly_catalog_session


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

    response = app.test_client().get("/healthz")
    if response.status_code != 200 or response.get_data(as_text=True) != "ok":
        raise RuntimeError("Health check failed after PostgreSQL initialization.")

    print("PostgreSQL application smoke check passed.")


if __name__ == "__main__":
    main()
