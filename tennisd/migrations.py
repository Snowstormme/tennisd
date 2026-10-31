"""Ordered schema migrations for the owner connection."""

from sqlalchemy import text

from . import db


MIGRATIONS = (
    ("0001_baseline", "Create the SQLAlchemy metadata schema."),
)


def _ensure_ledger(connection):
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS tennisd_schema_migration (
            version VARCHAR(64) PRIMARY KEY,
            description VARCHAR(240) NOT NULL,
            applied_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))


def run_migrations(app):
    """Apply every migration not recorded in the schema ledger."""
    with app.app_context(), db.engine.begin() as connection:
        _ensure_ledger(connection)
        if db.engine.dialect.name == "postgresql":
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": "tennisd-schema-migrations"},
            )

        applied = {
            row.version
            for row in connection.execute(text(
                "SELECT version FROM tennisd_schema_migration ORDER BY version"
            ))
        }
        for version, description in MIGRATIONS:
            if version in applied:
                continue
            if version == "0001_baseline":
                db.metadata.create_all(bind=connection)
            else:  # pragma: no cover - protects the registry from silent omissions
                raise RuntimeError(f"Migration {version} has no implementation.")
            connection.execute(
                text(
                    "INSERT INTO tennisd_schema_migration "
                    "(version, description) VALUES (:version, :description)"
                ),
                {"version": version, "description": description},
            )
            print(f"Applied migration {version}: {description}")
