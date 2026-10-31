import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect, text

from tennisd import create_app, db
from tennisd.migrations import run_migrations


class MigrationTests(unittest.TestCase):
    def test_baseline_is_recorded_once_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True,
                "SECRET_KEY": "migration-test-secret",
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(directory) / 'migration.db'}",
                "AUTO_CREATE_DB": False,
            })
            run_migrations(app)
            run_migrations(app)
            with app.app_context():
                version_count = db.session.execute(text(
                    "SELECT COUNT(*) FROM tennisd_schema_migration "
                    "WHERE version = '0001_baseline'"
                )).scalar_one()
                self.assertEqual(version_count, 1)
                self.assertTrue(inspect(db.engine).has_table("user"))
                db.session.remove()
                db.engine.dispose()
