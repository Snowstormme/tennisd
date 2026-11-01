"""Shared HTTP helpers used by the site routes."""

from flask import abort, current_app, request, url_for
from urllib.parse import urlsplit

from . import db


def database_id(value):
    """Parse positive PostgreSQL integer IDs without unbounded conversion."""
    if isinstance(value, int):
        return value if 1 <= value <= 2147483647 else None
    if not isinstance(value, str) or not 1 <= len(value) <= 10 or not value.isascii() or not value.isdigit():
        return None
    number = int(value)
    return number if 1 <= number <= 2147483647 else None


def require_database_id(value):
    identifier = database_id(value)
    if identifier is None:
        abort(404)
    return identifier


def schema_table_exists(table_name):
    """Return a migration-backed schema capability without querying per request."""
    return table_name in current_app.extensions.get("schema_capabilities", ())


def shared_cache(response, *, browser_seconds, cdn_seconds, stale_seconds):
    """Cache public, user-independent responses without making them feel stale."""
    response.headers["Cache-Control"] = (
        f"public, max-age={browser_seconds}, stale-while-revalidate={stale_seconds}"
    )
    response.headers["Vercel-CDN-Cache-Control"] = (
        f"max-age={cdn_seconds}, stale-while-revalidate={stale_seconds}"
    )
    return response


def safe_next(default="site.home"):
    target = request.args.get("next", "")
    parsed = urlsplit(target)
    return (
        target if target.startswith("/") and not target.startswith("//")
        and "\\" not in target and not parsed.scheme and not parsed.netloc
        else url_for(default)
    )


def public_url(path):
    return f"{current_app.config['PUBLIC_BASE_URL'].rstrip('/')}{path}"


def xml_response(body):
    from flask import Response

    return shared_cache(
        Response(body, content_type="application/xml; charset=utf-8"),
        browser_seconds=3600, cdn_seconds=86400, stale_seconds=86400,
    )
