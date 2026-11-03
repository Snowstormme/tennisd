import unittest
from sqlalchemy import select
from tennisd import db
from tennisd.catalog_audit import readonly_catalog_session
from tennisd.models import Match
from tennisd.tournament_name_audit import tournament_name_report
import test_tournament_urls as fixtures


class TournamentNameAuditTests(unittest.TestCase):
    names = fixtures.TournamentURLTests.names
    setUp = fixtures.TournamentURLTests.setUp
    tearDown = fixtures.TournamentURLTests.tearDown

    def test_readonly_report_preserves_separate_identities_and_excludes_hidden_names(self):
        with self.app.app_context():
            before = db.session.execute(select(Match.id, Match.tournament).order_by(Match.id)).all()
            db.session.rollback()
            with readonly_catalog_session():
                report = tournament_name_report()
            groups = {g['slug']: g for g in report['groups']}
            self.assertEqual({v['name'] for v in groups['choice-open']['variants']}, set(self.names))
            self.assertEqual([v['matches'] for v in groups['choice-open']['variants']], [37, 37])
            self.assertEqual(len(groups['event']['variants']), 2)
            self.assertNotIn('Choice Hidden', str(report))
            self.assertNotIn('email', str(report))
            self.assertEqual(before, db.session.execute(select(Match.id, Match.tournament).order_by(Match.id)).all())

    def test_limit_and_order_are_explicit(self):
        with self.app.app_context(), readonly_catalog_session():
            report = tournament_name_report(1)
            self.assertEqual(report['returned_groups'], 1)
            self.assertTrue(report['has_more'])
            self.assertGreaterEqual(report['collision_group_count'], 2)
            self.assertEqual(report['groups'][0]['slug'], 'choice-open')
            for invalid in (0, 501, True, '1'):
                with self.assertRaises(ValueError):
                    tournament_name_report(invalid)
