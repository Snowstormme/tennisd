"""Scope public evidence before review; cursor pages never change cohort totals."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from pathlib import Path
from unittest.mock import patch

from tennisd import create_app, db
from tennisd.catalog_audit import catalog_cohort_report, catalog_report, readonly_catalog_session
from tennisd.models import Match, Player, Review, User
from scripts.audit_catalog import main


class CatalogCohortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "cohort-tests-only",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.temp.name) / 'test.db'}"})
        self.context = self.app.app_context(); self.context.push()
        players = [Player(id="cohort-a", tour="ATP", name="Cohort A"), Player(id="cohort-b", tour="ATP", name="Cohort B"),
                   Player(id="cohort-w", tour="WTA", name="Cohort W"), Player(id="cohort-x", tour="WTA", name="Cohort X")]
        db.session.add_all(players); db.session.flush()
        specs = [("01", "ATP", 2024, "tennis_api", "Paris Audit", "Q4", players[0], players[1], 1, 1),
                 ("02", "ATP", 2024, "tennis_api", "Paris Audit", "R128", players[0], players[2], 12, 31),
                 ("03", "ATP", 2024, "livetennisapi", "Paris Audit", "F", players[0], players[1], 1, 1),
                 ("04", "WTA", 2024, "tennis_api", "Paris Audit", "F", players[2], players[3], 1, 1),
                 ("05", "ATP", 2025, "tennis_api", "Paris Audit", "SF", players[0], players[1], 1, 1),
                 ("06", "ATP", 2024, "tennis_api", "Paris Audit Extra", "SF", players[0], players[1], 1, 1)]
        for identifier, tour, year, provider, event, round_code, winner, loser, month, day in specs:
            db.session.add(Match(id="cohort-" + identifier, tour=tour, provider=provider, tournament=event,
                week_start=date(year,month,day), round=round_code, status="finished", surface="Hard", level="M",
                score="6-4 6-4", winner=winner, loser=loser))
        user = User(username="cohortprivate", email="cohort-private@example.test", display_name="Private", password_hash="do-not-export")
        db.session.add(Review(user=user, match_id="cohort-01", watched_on=date(2026,11,1), is_public=False, body="COHORT_PRIVATE_NOTE"))
        db.session.commit()
        self.scope = {"tour": "ATP", "year": 2024, "provider": "tennis_api", "tournament": "PARIS AUDIT"}

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.context.pop(); self.temp.cleanup()

    def test_filters_include_valid_and_invalid_rows_but_no_private_data(self):
        with readonly_catalog_session():
            report = catalog_cohort_report(**self.scope)
        self.assertEqual(report["counts"], {"matches": 2, "identity_conflicts": 1})
        self.assertEqual([row["id"] for row in report["samples"]], ["cohort-01", "cohort-02"])
        self.assertEqual(report["samples"][0]["round"], "Q4")
        self.assertEqual(report["round_distribution"], [{"round": "Q4", "count": 1}, {"round": "R128", "count": 1}])
        for secret in ("cohortprivate", "cohort-private@example.test", "COHORT_PRIVATE_NOTE", "do-not-export"):
            self.assertNotIn(secret, json.dumps(report))

    def test_cursor_has_no_omissions_and_keeps_full_cohort_totals(self):
        with readonly_catalog_session():
            first = catalog_cohort_report(limit=1, **self.scope)
            second = catalog_cohort_report(limit=1, after_id=first["next_after_id"], **self.scope)
            empty = catalog_cohort_report(limit=1, after_id="cohort-99", **self.scope)
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_after_id"])
        self.assertEqual([first["samples"][0]["id"], second["samples"][0]["id"]], ["cohort-01", "cohort-02"])
        self.assertEqual(first["counts"], second["counts"])
        self.assertEqual(empty["counts"], first["counts"])
        self.assertEqual(empty["samples"], [])

    def test_tournament_is_literal_not_prefix_pattern_or_sql(self):
        for value in ("Paris%", "Paris Audit' OR 1=1 --", "unknown"):
            with readonly_catalog_session():
                report = catalog_cohort_report(tournament=value)
            self.assertEqual(report["counts"]["matches"], 0)
            self.assertEqual(report["round_distribution"], [])

    def test_invalid_filters_are_refused_and_legacy_report_stays_compatible(self):
        for kwargs in ({"year": 2101}, {"year": "2024"}, {"tour": "OTHER"}, {"limit": 501}, {"limit": 0}, {"tournament": "a" * 121}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                catalog_cohort_report(**kwargs)
        with readonly_catalog_session():
            report = catalog_report(1)
        self.assertEqual(report["schema_version"], 2)
        self.assertIn("cross_tour_or_self_matches", report)

    def test_cli_rejects_invalid_input_before_opening_database(self):
        for arguments in (("--year", "no"), ("--year", "2101"), ("--tour", "OTHER"), ("--limit", "501")):
            with self.subTest(arguments=arguments), patch("sys.argv", ["audit_catalog.py", *arguments]), \
                 patch("scripts.audit_catalog.create_app") as create, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code, 2)
                create.assert_not_called()
