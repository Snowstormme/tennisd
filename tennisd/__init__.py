import os
import secrets
from datetime import timedelta
from pathlib import Path

import click
from flask import Flask, current_app, render_template, request, session
from flask_login import current_user
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from sqlalchemy import event, text
from sqlalchemy.engine import Engine, make_url
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "site.login"


@event.listens_for(Engine, "connect")
def enforce_sqlite_foreign_keys(connection, _record):
    if connection.__class__.__module__.startswith("sqlite3"):
        connection.execute("PRAGMA foreign_keys=ON")


def create_app(test_config=None):
    static_folder = Path(__file__).resolve().parent.parent / "public" / "static"
    flask_options = {"static_folder": str(static_folder)}
    if os.environ.get("VERCEL"):
        flask_options["instance_path"] = "/tmp/tennisd-instance"
    app = Flask(__name__, **flask_options)
    instance_path = Path(app.instance_path)
    instance_path.mkdir(parents=True, exist_ok=True)

    database_url = os.environ.get("DATABASE_URL")
    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    if database_url and database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    production = os.environ.get("APP_ENV") == "production"
    if production and database_url and database_url.startswith("postgresql+psycopg://"):
        parsed = make_url(database_url)
        mode = parsed.query.get("sslmode", "require")
        if mode not in ("require", "verify-ca", "verify-full"):
            raise RuntimeError("Production PostgreSQL must require TLS.")
        database_url = parsed.update_query_dict({"sslmode": mode}).render_as_string(
            hide_password=False
        )

    public_host = (
        os.environ.get("PUBLIC_HOST")
        or os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
        or os.environ.get("VERCEL_URL")
        or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    )
    trusted_hosts = list(dict.fromkeys(filter(None, (
        public_host,
        os.environ.get("VERCEL_PROJECT_PRODUCTION_URL"),
        os.environ.get("VERCEL_URL"),
    ))))

    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        SQLALCHEMY_DATABASE_URI=database_url or f"sqlite:///{instance_path / 'tennisd.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        TRUSTED_HOSTS=trusted_hosts if production and trusted_hosts else None,
        PUBLIC_BASE_URL=f"https://{public_host}" if production and public_host else "http://127.0.0.1:5000",
        REQUIRE_EMAIL_VERIFICATION=os.environ.get(
            "REQUIRE_EMAIL_VERIFICATION", "true" if production else "false"
        ).lower() == "true",
        REGISTRATION_ENABLED=os.environ.get("REGISTRATION_ENABLED", "true") == "true",
        RESEND_API_KEY=os.environ.get("RESEND_API_KEY", ""),
        MAIL_FROM=os.environ.get("MAIL_FROM", ""),
        MAIL_DELIVERY=None,
        ADMIN_EMAIL=os.environ.get("ADMIN_EMAIL", "").strip().lower(),
        CONTACT_EMAIL=os.environ.get("CONTACT_EMAIL", "").strip().lower(),
        AUTO_CREATE_DB=os.environ.get("AUTO_CREATE_DB", "false" if production else "true") == "true",
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        REMEMBER_COOKIE_SECURE=production,
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_DURATION=timedelta(days=30),
    )
    if production:
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_pre_ping": True, "pool_recycle": 1800,
            "pool_size": 2, "max_overflow": 1,
        }
    if test_config:
        app.config.update(test_config)
    if production:
        if not os.environ.get("SECRET_KEY") or len(app.secret_key) < 32:
            raise RuntimeError("Set a stable SECRET_KEY of at least 32 characters in production.")
        if not app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgresql+psycopg://"):
            raise RuntimeError("Production requires a persistent PostgreSQL DATABASE_URL.")
        if not app.config["TRUSTED_HOSTS"]:
            raise RuntimeError("Set PUBLIC_HOST or RENDER_EXTERNAL_HOSTNAME in production.")
        if app.config["AUTO_CREATE_DB"]:
            raise RuntimeError("Production must initialize the database outside the web process.")
        if (
            app.config["REGISTRATION_ENABLED"]
            and app.config["REQUIRE_EMAIL_VERIFICATION"]
            and (
                not app.config["RESEND_API_KEY"] or not app.config["MAIL_FROM"]
            )
        ):
            raise RuntimeError("Email delivery must be configured before opening registration.")
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    db.init_app(app)
    login_manager.init_app(app)

    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        parts = user_id.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            return None
        user = db.session.get(User, int(parts[0]))
        if user is None:
            return None
        version = user.auth_state.session_version if user.auth_state else 0
        return user if version == int(parts[1]) else None

    @app.context_processor
    def csrf_context():
        form_endpoints = {
            "site.register", "site.login", "site.check_email",
            "site.resend_verification", "site.verify_email",
            "site.forgot_password", "site.reset_password",
        }
        if (current_user.is_authenticated or request.endpoint in form_endpoints) and "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return {"csrf_token": session.get("csrf_token", "")}

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https://commons.wikimedia.org https://upload.wikimedia.org https://thumb.wikimedia.org "
            "https://www.atptour.com https://photoresources.wtatennis.com https://content.wimbledon.com "
            "https://ichef.bbci.co.uk https://a3.espncdn.com https://e0.365dm.com "
            "https://d2me2qg8dfiw8u.cloudfront.net; "
            "connect-src 'self'; form-action 'self'; base-uri 'self'; "
            "frame-ancestors 'none'; object-src 'none'"
        )
        if production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.endpoint == "static" and request.args.get("v"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            response.headers["Vercel-CDN-Cache-Control"] = "max-age=31536000"
        elif current_user.is_authenticated:
            response.cache_control.no_store = True
            response.cache_control.private = True
        elif (
            request.method == "GET"
            and response.status_code == 200
            and request.endpoint in {
                "site.home", "site.matches", "site.players", "site.tournaments",
                "site.news", "site.search", "site.about", "site.privacy",
                "site.player_detail", "site.match_detail", "site.tournament_detail",
                "site.tournament_edition_detail", "site.news_story",
            }
        ):
            # Keep anonymous HTML briefly in the visitor's browser. This makes a
            # prefetched section open immediately without sharing personalized
            # pages through a CDN cache.
            response.cache_control.private = True
            response.cache_control.max_age = 60
            response.cache_control.stale_while_revalidate = 300
        return response

    from .routes import site

    app.register_blueprint(site)

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(429)
    @app.errorhandler(500)
    @app.errorhandler(503)
    def friendly_error(error):
        return render_template("error.html", code=error.code), error.code

    @app.get("/healthz")
    def healthz():
        db.session.execute(text("SELECT 1"))
        return "ok", 200, {"Cache-Control": "no-store"}

    @app.cli.command("init-db")
    def init_db():
        """Create tables using a database owner connection."""
        db.create_all()
        click.echo("Database initialized.")

    if app.config["AUTO_CREATE_DB"]:
        with app.app_context():
            db.create_all()
            if app.config["TESTING"]:
                from .importer import seed_samples
                seed_samples()

    return app
