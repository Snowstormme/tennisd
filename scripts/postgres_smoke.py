"""Create the application schema and run a minimal PostgreSQL smoke check."""

import os

from sqlalchemy import text

from tennisd import create_app, db
from tennisd.migrations import run_migrations


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

    response = app.test_client().get("/healthz")
    if response.status_code != 200 or response.get_data(as_text=True) != "ok":
        raise RuntimeError("Health check failed after PostgreSQL initialization.")

    print("PostgreSQL application smoke check passed.")


if __name__ == "__main__":
    main()
