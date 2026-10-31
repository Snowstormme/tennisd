"""News feed and article proxy routes."""

from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests
from flask import Response, abort, current_app, jsonify, render_template, request

from .news_feed import NEWS_SOURCES, curate_news_items, fetch_news_article, fetch_news_items
from .web import shared_cache


def register_news_routes(site):
    """Register news routes on the shared site blueprint."""

    @site.get("/news")
    def news():
        source_keys = {source["key"] for source in NEWS_SOURCES}
        selected_source = request.args.get("source", "all").strip().lower()
        if selected_source not in source_keys:
            selected_source = "all"
        all_stories = [] if current_app.config["TESTING"] else curate_news_items(
            fetch_news_items(selected_source), minimum=8 if selected_source != "all" else 0,
        )
        try:
            page = max(1, int(request.args.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        page_size = 30
        total_stories = len(all_stories)
        total_pages = max(1, (total_stories + page_size - 1) // page_size)
        page = min(page, total_pages)
        stories = all_stories[(page - 1) * page_size:page * page_size]
        featured_story = stories[0] if page == 1 and stories else None
        featured_article = None
        if featured_story is not None:
            featured_article = fetch_news_article(
                featured_story["source_key"], featured_story["id"], featured_story["url"]
            )
        feed_stories = stories[1:] if featured_story is not None else stories
        return render_template(
            "news.html", sources=NEWS_SOURCES, stories=stories, feed_stories=feed_stories,
            featured_story=featured_story, featured_article=featured_article, selected_source=selected_source,
            total_stories=total_stories, page=page, total_pages=total_pages,
        )

    @site.get("/api/news-status")
    def news_status():
        source_keys = {source["key"] for source in NEWS_SOURCES}
        selected_source = request.args.get("source", "all").strip().lower()
        if selected_source not in source_keys:
            selected_source = "all"
        stories = [] if current_app.config["TESTING"] else curate_news_items(
            fetch_news_items(selected_source), minimum=8 if selected_source != "all" else 0,
        )
        response = jsonify({
            "first_id": stories[0]["id"] if stories else None,
            "total": len(stories),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        })
        return shared_cache(
            response, browser_seconds=60, cdn_seconds=300, stale_seconds=600,
        )

    @site.get("/news/<source_key>/<story_id>")
    def news_story(source_key, story_id):
        if source_key not in {source["key"] for source in NEWS_SOURCES}:
            abort(404)
        story = next((item for item in fetch_news_items(source_key) if item["id"] == story_id), None)
        if story is None:
            abort(404)
        article = fetch_news_article(source_key, story_id, story["url"])
        return render_template("news_story.html", story=story, article=article)

    @site.get("/news/<source_key>/<story_id>/image")
    def news_image(source_key, story_id):
        source = next((item for item in NEWS_SOURCES if item["key"] == source_key), None)
        if source is None:
            abort(404)
        story = next((item for item in fetch_news_items(source_key) if item["id"] == story_id), None)
        if story is None:
            abort(404)
        article = fetch_news_article(source_key, story_id, story["url"])
        image_url = article.get("image", "") if article else ""
        hostname = (urlsplit(image_url).hostname or "").lower()
        allowed_domain = source["image_domain"]
        if not image_url.startswith("https://") or not (
            hostname == allowed_domain or hostname.endswith(f".{allowed_domain}")
        ):
            abort(404)
        try:
            upstream = requests.get(
                image_url, headers={"Accept": "image/avif,image/webp,image/png,image/jpeg", "User-Agent": "Tennisd/1.0"},
                timeout=8,
            )
            upstream.raise_for_status()
        except requests.RequestException:
            abort(404)
        content_type = upstream.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if content_type not in {"image/avif", "image/jpeg", "image/png", "image/webp"} or len(upstream.content) > 5_000_000:
            abort(404)
        response = Response(upstream.content, mimetype=content_type)
        return shared_cache(
            response, browser_seconds=86400, cdn_seconds=604800,
            stale_seconds=604800,
        )
