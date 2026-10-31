import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func, select

from tennisd import create_app, db
from tennisd.models import Notification, User
from tennisd.notifications import create_notification


class NotificationTests(unittest.TestCase):
    def test_duplicate_alerts_are_suppressed_for_a_day(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True,
                "SECRET_KEY": "notification-test-secret",
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(directory) / 'notifications.db'}",
            })
            with app.app_context():
                user = User(username="alice", email="alice@example.com", display_name="Alice")
                user.set_password("long-test-password")
                db.session.add(user)
                db.session.commit()
                self.assertTrue(create_notification(
                    user.id, kind="followed_player_match", title="Live match",
                    body="A vs B", url="/live-matches/1",
                ))
                db.session.commit()
                self.assertFalse(create_notification(
                    user.id, kind="followed_player_match", title="Live match",
                    body="A vs B", url="/live-matches/1",
                ))
                self.assertEqual(db.session.scalar(select(func.count(Notification.id))), 1)
