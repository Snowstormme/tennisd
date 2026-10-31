"""Apply the ordered Tennisd schema migrations with the owner connection."""

import os

from tennisd import create_app
from tennisd.migrations import run_migrations


def main():
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("DATABASE_URL must point to PostgreSQL for migrations.")
    app = create_app({"AUTO_CREATE_DB": False})
    run_migrations(app)
    print("Database migrations are up to date.")


if __name__ == "__main__":
    main()
