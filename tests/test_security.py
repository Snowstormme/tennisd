import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tennisd import create_app, db
from tennisd.models import Match, User
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash


class AccountSecurityFlows(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.sent = []
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-only-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.temporary.name) / 'test.db'}",
            "REQUIRE_EMAIL_VERIFICATION": True,
            "PUBLIC_BASE_URL": "https://tennisd.example",
            "MAIL_DELIVERY": lambda email, subject, body: self.sent.append((email, subject, body)),
        })
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temporary.cleanup()

    def token(self, client=None):
        with (client or self.client).session_transaction() as session:
            return session["csrf_token"]

    def register(self):
        self.client.get("/register")
        return self.client.post("/register", data={
            "csrf_token": self.token(), "username": "alice",
            "email": "alice@example.com", "password": "long-test-password",
        })

    def verify(self):
        code = re.search(r"\b\d{6}\b", self.sent[-1][2]).group()
        self.assertEqual(self.client.get("/check-email").status_code, 200)
        response = self.client.post("/check-email", data={
            "csrf_token": self.token(), "email": "alice@example.com", "code": code,
        })
        self.assertEqual(response.headers["Location"], "/login")
        return code

    def test_public_catalog_does_not_create_session_and_uses_browser_cache(self):
        response = self.client.get("/players")
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("max-age=60", response.headers["Cache-Control"])
        with self.client.session_transaction() as session:
            self.assertNotIn("csrf_token", session)

        form = self.client.get("/register")
        self.assertIn("Set-Cookie", form.headers)
        with self.client.session_transaction() as session:
            self.assertTrue(session["csrf_token"])

    def test_email_verification_is_required_and_single_use(self):
        self.assertEqual(self.register().headers["Location"], "/check-email")
        self.client.get("/login")
        response = self.client.post("/login", data={
            "csrf_token": self.token(), "identity": "alice", "password": "long-test-password",
        })
        self.assertEqual(response.headers["Location"], "/check-email")
        code = self.verify()
        response = self.client.post("/check-email", data={
            "csrf_token": self.token(), "email": "alice@example.com", "code": code,
        }, follow_redirects=True)
        self.assertIn(b"incorrect or has expired", response.data)
        response = self.client.post("/login", data={
            "csrf_token": self.token(), "identity": "alice", "password": "long-test-password",
        })
        self.assertEqual(response.headers["Location"], "/me")
        self.assertEqual(self.client.get("/me").status_code, 302)

    def test_reset_invalidates_sessions_and_old_password(self):
        self.register()
        self.verify()
        self.client.get("/login")
        self.client.post("/login", data={
            "csrf_token": self.token(), "identity": "alice", "password": "long-test-password",
        })
        another = self.app.test_client()
        another.get("/forgot-password")
        another.post("/forgot-password", data={
            "csrf_token": self.token(another), "email": "alice@example.com",
        })
        link = re.search(r"https://tennisd\.example/reset-password/\S+", self.sent[-1][2]).group()
        path = link.removeprefix("https://tennisd.example")
        self.assertEqual(another.get(path).status_code, 200)
        self.assertEqual(another.post(path, data={"password": "another-long-password"}).status_code, 400)
        self.assertEqual(another.post(path, data={
            "csrf_token": self.token(another), "password": "another-long-password",
        }).headers["Location"], "/login")
        self.assertEqual(another.get(path).headers["Location"], "/forgot-password")
        self.assertEqual(self.client.get("/me").headers["Location"], "/login?next=%2Fme")
        self.client.get("/login")
        self.assertEqual(self.client.post("/login", data={
            "csrf_token": self.token(), "identity": "alice", "password": "long-test-password",
        }).status_code, 200)
        self.assertEqual(self.client.post("/login", data={
            "csrf_token": self.token(), "identity": "alice", "password": "another-long-password",
        }).headers["Location"], "/me")

    def test_resend_replaces_the_previous_verification_code(self):
        self.register()
        first_code = re.search(r"\b\d{6}\b", self.sent[-1][2]).group()
        self.client.get("/resend-verification")
        self.client.post("/resend-verification", data={
            "csrf_token": self.token(), "email": "alice@example.com",
        })
        second_code = re.search(r"\b\d{6}\b", self.sent[-1][2]).group()
        self.assertNotEqual(first_code, second_code)

        rejected = self.client.post("/check-email", data={
            "csrf_token": self.token(), "email": "alice@example.com", "code": first_code,
        }, follow_redirects=True)
        self.assertIn(b"incorrect or has expired", rejected.data)
        accepted = self.client.post("/check-email", data={
            "csrf_token": self.token(), "email": "alice@example.com", "code": second_code,
        })
        self.assertEqual(accepted.headers["Location"], "/login")

    def test_login_limit_and_security_headers(self):
        self.register()
        self.client.get("/login")
        for _ in range(8):
            self.assertEqual(self.client.post("/login", data={
                "csrf_token": self.token(), "identity": "alice", "password": "wrong-password",
            }).status_code, 200)
        blocked = self.client.post("/login", data={
            "csrf_token": self.token(), "identity": "alice", "password": "wrong-password",
        })
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", blocked.headers["Content-Security-Policy"])

    def test_legacy_password_hash_upgrades_after_login(self):
        self.app.config["REQUIRE_EMAIL_VERIFICATION"] = False
        self.register()
        self.client.get("/u/alice")
        self.client.post("/logout", data={"csrf_token": self.token()})
        with self.app.app_context():
            user = db.session.scalar(select(User).where(User.username == "alice"))
            user.password_hash = generate_password_hash("long-test-password")
            db.session.commit()
        self.client.get("/login")
        self.client.post("/login", data={
            "csrf_token": self.token(), "identity": "alice", "password": "long-test-password",
        })
        with self.app.app_context():
            user = db.session.scalar(select(User).where(User.username == "alice"))
            self.assertTrue(user.password_hash.startswith("$argon2id$"))


class ProductionConfig(unittest.TestCase):
    def test_missing_production_secrets_fail_closed(self):
        with patch.dict(os.environ, {"APP_ENV": "production", "SECRET_KEY": "", "DATABASE_URL": ""}):
            with self.assertRaisesRegex(RuntimeError, "SECRET_KEY"):
                create_app({"TESTING": True})

    def test_catalog_bootstrap_command(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True,
                "SECRET_KEY": "test-only-secret",
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(directory) / 'catalog.db'}",
                "AUTO_CREATE_DB": False,
                "SEED_FULL_CATALOG": True,
            })
            result = app.test_cli_runner().invoke(args=["init-db"])
            self.assertEqual(result.exit_code, 0, result.output)
            with app.app_context():
                self.assertEqual(db.session.scalar(select(func.count(Match.id))), 19903)
                db.session.remove()
                db.engine.dispose()

    def test_vercel_hostname_configures_production_trust(self):
        environment = {
            "APP_ENV": "production",
            "SECRET_KEY": "v" * 64,
            "DATABASE_URL": "postgresql://user:password@example.com/app?sslmode=require",
            "VERCEL": "1",
            "VERCEL_PROJECT_PRODUCTION_URL": "tennisd.vercel.app",
            "VERCEL_URL": "tennisd-preview.vercel.app",
            "REGISTRATION_ENABLED": "false",
        }
        with patch.dict(os.environ, environment, clear=True):
            app = create_app({"TESTING": True})
        self.assertEqual(
            app.config["TRUSTED_HOSTS"],
            ["tennisd.vercel.app", "tennisd-preview.vercel.app"],
        )
        self.assertEqual(app.config["PUBLIC_BASE_URL"], "https://tennisd.vercel.app")
        self.assertEqual(app.instance_path, "/tmp/tennisd-instance")
        self.assertIn("user:password@", app.config["SQLALCHEMY_DATABASE_URI"])
        self.assertNotIn("***", app.config["SQLALCHEMY_DATABASE_URI"])

    def test_registration_can_open_without_email_verification(self):
        environment = {
            "APP_ENV": "production",
            "SECRET_KEY": "v" * 64,
            "DATABASE_URL": "postgresql://user:password@example.com/app?sslmode=require",
            "PUBLIC_HOST": "tennisd.example",
            "REGISTRATION_ENABLED": "true",
            "REQUIRE_EMAIL_VERIFICATION": "false",
        }
        with patch.dict(os.environ, environment, clear=True):
            app = create_app({"TESTING": True})
        self.assertTrue(app.config["REGISTRATION_ENABLED"])
        self.assertFalse(app.config["REQUIRE_EMAIL_VERIFICATION"])

    def test_verified_registration_requires_email_delivery(self):
        environment = {
            "APP_ENV": "production",
            "SECRET_KEY": "v" * 64,
            "DATABASE_URL": "postgresql://user:password@example.com/app?sslmode=require",
            "PUBLIC_HOST": "tennisd.example",
            "REGISTRATION_ENABLED": "true",
            "REQUIRE_EMAIL_VERIFICATION": "true",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Email delivery"):
                create_app({"TESTING": True})


if __name__ == "__main__":
    unittest.main()
