from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qs, quote, quote_plus, urljoin, urlparse
from defusedxml import ElementTree

import requests


NEWS_SOURCES = (
    {
        "key": "atp",
        "name": "ATP Tour",
        "url": "https://www.atptour.com/en/news",
        "domain": "atptour.com",
        "image_domain": "atptour.com",
        "search": "site:atptour.com/en/news",
        "description": "Men's tour reports, interviews and tournament updates.",
    },
    {
        "key": "wta",
        "name": "WTA",
        "url": "https://www.wtatennis.com/news",
        "domain": "wtatennis.com",
        "image_domain": "photoresources.wtatennis.com",
        "search": "site:wtatennis.com/news",
        "description": "Women's tour news, match reactions and player stories.",
    },
    {
        "key": "itf",
        "name": "ITF",
        "url": "https://www.itftennis.com/en/news-and-media/articles/",
        "domain": "itftennis.com",
        "image_domain": "media.itftennis.com",
        "search": "site:itftennis.com/en/news-and-media/articles",
        "description": "Grand Slam, team competition and world tennis news.",
    },
    {
        "key": "wimbledon",
        "name": "Wimbledon",
        "url": "https://www.wimbledon.com/en_GB/news/index.html",
        "domain": "wimbledon.com",
        "image_domain": "content.wimbledon.com",
        "search": "site:wimbledon.com/en_GB/news",
        "description": "Official Championships news and features.",
    },
    {
        "key": "bbc",
        "name": "BBC Sport",
        "url": "https://www.bbc.com/sport/tennis",
        "domain": "bbc.com",
        "image_domain": "ichef.bbci.co.uk",
        "search": "site:bbc.com/sport/tennis",
        "description": "International tennis reporting, results and major tournament coverage.",
    },
    {
        "key": "espn",
        "name": "ESPN Tennis",
        "url": "https://www.espn.com/tennis/",
        "domain": "espn.com",
        "image_domain": "a3.espncdn.com",
        "search": "site:espn.com/tennis",
        "description": "Breaking tennis news, tournament reporting and player updates.",
    },
    {
        "key": "sky",
        "name": "Sky Sports",
        "url": "https://www.skysports.com/tennis",
        "domain": "skysports.com",
        "image_domain": "e0.365dm.com",
        "search": "site:skysports.com/tennis",
        "description": "Tour news, interviews and reporting from major tennis events.",
    },
    {
        "key": "tennis365",
        "name": "Tennis365",
        "url": "https://www.tennis365.com/",
        "domain": "tennis365.com",
        "image_domain": "d2me2qg8dfiw8u.cloudfront.net",
        "search": "site:tennis365.com",
        "description": "Daily tour reporting, analysis and player stories.",
    },
)

IMPORTANT_NEWS_PATTERNS = (
    r"\b(?:wins?|claims?|lifts?|secures?) (?:the )?title\b", r"\bcrowned champion\b",
    r"\bworld no\.? ?1\b",
    r"\bnew no\.? ?1\b", r"\bretires?\b", r"\bretirement\b", r"\binjur(?:y|ed)\b",
    r"\bwithdraws?\b", r"\bpulls? out\b", r"\brecord\b", r"\bsuspend(?:ed|s|ion)\b",
    r"\bbanned?\b", r"\bdoping\b",
)
IMPORTANT_NEWS_RE = re.compile("|".join(IMPORTANT_NEWS_PATTERNS), re.IGNORECASE)


def _published_at(value):
    try:
        parsed = parsedate_to_datetime(value or "")
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


@lru_cache(maxsize=64)
def _source_items(source_key, cache_window):
    source = next(item for item in NEWS_SOURCES if item["key"] == source_key)
    query = quote_plus(source["search"])
    response = requests.get(
        f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
        headers={"Accept": "application/rss+xml", "User-Agent": "Tennisd/1.0 (news reader)"},
        timeout=5,
    )
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    stories = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        publisher = (item.findtext("source") or "").strip()
        for publisher_name in (publisher, source["name"]):
            suffix = f" - {publisher_name}"
            if publisher_name and title.endswith(suffix):
                title = title[:-len(suffix)].strip()
                break
        if source_key == "tennis365" and re.search(r"\bChallenger\b.*\d{2}/\d{2}/\d{4}$", title):
            continue
        if source_key == "sky" and title.startswith("Sports News, Transfers, Scores"):
            continue
        link = (item.findtext("link") or "").strip()
        if not title or not link.startswith("https://news.google.com/"):
            continue
        published_at = _published_at(item.findtext("pubDate"))
        stories.append({
            "id": urlparse(link).path.rsplit("/", 1)[-1],
            "title": title,
            "url": link,
            "source_key": source["key"],
            "source": source["name"],
            "published_at": published_at,
        })
    return stories


def fetch_news_items(source_key="all", now=None):
    """Load official publisher headlines through Google News RSS for discovery."""
    now = now or datetime.now(timezone.utc)
    cache_window = int(now.timestamp() // 60)
    keys = [source_key] if source_key != "all" else [source["key"] for source in NEWS_SOURCES]

    def load(key):
        try:
            return _source_items(key, cache_window)
        except (ElementTree.ParseError, StopIteration, requests.RequestException):
            return []

    with ThreadPoolExecutor(max_workers=len(keys)) as executor:
        groups = executor.map(load, keys)
    stories = [story for group in groups for story in group]
    stories.sort(key=lambda story: story["published_at"], reverse=True)
    return stories


def curate_news_items(
    stories, now=None, recent_hours=48, weekly_days=7, weekly_limit=28,
    important_days=14, important_limit=6, minimum=0, fallback_days=365,
):
    """Keep all new headlines, a useful week view, and a few major older stories."""
    now = now or datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(hours=recent_hours)
    weekly_cutoff = now - timedelta(days=weekly_days)
    important_cutoff = now - timedelta(days=important_days)
    recent, weekly, important, seen_titles = [], [], [], set()
    for story in stories:
        published_at = story.get("published_at")
        if not published_at or published_at > now + timedelta(hours=2):
            continue
        normalized_title = re.sub(r"\W+", " ", story.get("title", "").lower()).strip()
        if not normalized_title or normalized_title in seen_titles:
            continue
        seen_titles.add(normalized_title)
        if published_at >= recent_cutoff:
            recent.append(story)
        elif published_at >= weekly_cutoff:
            weekly.append(story)
        elif published_at >= important_cutoff and IMPORTANT_NEWS_RE.search(story.get("title", "")):
            important.append(story)
    selected = recent + weekly[:weekly_limit] + important[:important_limit]
    selected_ids = {story.get("id") for story in selected}
    selected_titles = {
        re.sub(r"\W+", " ", story.get("title", "").lower()).strip() for story in selected
    }
    fallback_cutoff = now - timedelta(days=fallback_days)
    for story in stories:
        if len(selected) >= minimum:
            break
        published_at = story.get("published_at")
        normalized_title = re.sub(r"\W+", " ", story.get("title", "").lower()).strip()
        if (
            published_at and fallback_cutoff <= published_at <= now + timedelta(hours=2)
            and story.get("id") not in selected_ids and normalized_title not in selected_titles
        ):
            selected.append(story)
            selected_ids.add(story.get("id"))
            selected_titles.add(normalized_title)
    selected.sort(key=lambda story: story["published_at"], reverse=True)
    return selected


def _google_article_url(article_id, source_url):
    query = parse_qs(urlparse(source_url).query)
    locale = {
        "hl": query.get("hl", ["en-US"])[0],
        "gl": query.get("gl", ["US"])[0],
        "ceid": query.get("ceid", ["US:en"])[0],
    }
    params_url = (
        f"https://news.google.com/rss/articles/{article_id}"
        f"?hl={quote(locale['hl'])}&gl={quote(locale['gl'])}&ceid={quote(locale['ceid'])}"
    )
    response = requests.get(params_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
    response.raise_for_status()
    signature = re.search(r'data-n-a-sg="([^"]+)"', response.text)
    timestamp = re.search(r'data-n-a-ts="([^"]+)"', response.text)
    if not signature or not timestamp:
        return None
    context = [
        ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
        "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
    ]
    inner = ["garturlreq", context, article_id, int(timestamp.group(1)), signature.group(1)]
    envelope = ["Fbv4je", json.dumps(inner, separators=(",", ":")), None, "0"]
    body = "f.req=" + quote(json.dumps([[envelope]], separators=(",", ":")))
    decoded = requests.post(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": "Mozilla/5.0",
        },
        timeout=5,
    )
    decoded.raise_for_status()
    match = re.search(r'\[\\"garturlres\\",\\"(https?://[^"\\]+)', decoded.text)
    return match.group(1).replace("\\u003d", "=").replace("\\u0026", "&") if match else None


class _ArticlePreviewParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.stack = []
        self.paragraph = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.stack.append(tag)
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key and attrs.get("content"):
                self.meta[key.lower()] = attrs["content"].strip()
        if tag == "p" and ("article" in self.stack or "main" in self.stack):
            self.paragraph = []

    def handle_endtag(self, tag):
        if tag == "p" and self.paragraph is not None:
            text = re.sub(r"\s+", " ", "".join(self.paragraph)).strip()
            if len(text) >= 45:
                self.meta.setdefault("paragraphs", []).append(text)
            self.paragraph = None
        if tag in self.stack:
            position = len(self.stack) - 1 - self.stack[::-1].index(tag)
            self.stack = self.stack[:position]

    def handle_data(self, data):
        if self.paragraph is not None and not any(tag in self.stack for tag in ("script", "style", "nav", "footer")):
            self.paragraph.append(data)


@lru_cache(maxsize=128)
def fetch_news_article(source_key, article_id, google_url):
    source = next((item for item in NEWS_SOURCES if item["key"] == source_key), None)
    if source is None or not re.fullmatch(r"[A-Za-z0-9_-]{20,500}", article_id):
        return None
    try:
        original_url = _google_article_url(article_id, google_url)
        hostname = (urlparse(original_url).hostname or "").lower() if original_url else ""
        if not hostname or not (hostname == source["domain"] or hostname.endswith(f".{source['domain']}")):
            return {"original_url": google_url, "description": "", "paragraphs": [], "image": ""}
        response = requests.get(
            original_url,
            headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0 (compatible; Tennisd/1.0)"},
            timeout=7,
        )
        response.raise_for_status()
        if "text/html" not in response.headers.get("Content-Type", "") or len(response.content) > 3_000_000:
            return {"original_url": original_url, "description": "", "paragraphs": [], "image": ""}
        parser = _ArticlePreviewParser()
        parser.feed(response.text)
        description = unescape(parser.meta.get("og:description") or parser.meta.get("description") or "")
        paragraphs, total = [], 0
        for paragraph in parser.meta.get("paragraphs", []):
            if paragraph == description or paragraph in paragraphs:
                continue
            if total + len(paragraph) > 1400:
                break
            paragraphs.append(paragraph)
            total += len(paragraph)
            if len(paragraphs) == 3:
                break
        image = (
            parser.meta.get("og:image") or parser.meta.get("twitter:image")
            or parser.meta.get("twitter:image:src") or ""
        )
        return {
            "original_url": original_url,
            "description": description[:500],
            "paragraphs": paragraphs,
            "image": urljoin(original_url, image) if image else "",
        }
    except (ValueError, requests.RequestException):
        return {"original_url": google_url, "description": "", "paragraphs": [], "image": ""}
