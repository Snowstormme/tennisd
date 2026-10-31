"""Shared HTTP helpers used by the site routes."""

from flask import current_app, request, url_for
from sqlalchemy import inspect
from urllib.parse import urlsplit

from . import db


def schema_table_exists(table_name):
    """Cache schema capability checks for the lifetime of an app process."""
    cache = current_app.extensions.setdefault("schema_table_cache", {})
    if table_name not in cache:
        cache[table_name] = inspect(db.engine).has_table(table_name)
    return cache[table_name]


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
