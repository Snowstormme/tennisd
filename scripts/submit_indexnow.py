"""Notify IndexNow search engines about Tennisd's frequently updated entry pages."""

import json
import os
import sys
from urllib.request import Request, urlopen


HOST = os.environ.get("PUBLIC_HOST", "tennisd.vercel.app").strip().removeprefix("https://").rstrip("/")
KEY = os.environ.get("INDEXNOW_KEY", "a4f23d7e946b4aa8b36740f15f67e2c1").strip()
PATHS = ("/", "/matches", "/matches/today", "/matches/this-week", "/matches/top-rated", "/players", "/tournaments", "/news")


def main():
    payload = json.dumps({
        "host": HOST,
        "key": KEY,
        "keyLocation": f"https://{HOST}/{KEY}.txt",
        "urlList": [f"https://{HOST}{path}" for path in PATHS],
    }).encode()
    request = Request(
        "https://api.indexnow.org/indexnow", data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
    )
    with urlopen(request, timeout=20) as response:
        print(f"IndexNow accepted {len(PATHS)} URLs (HTTP {response.status}).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"IndexNow submission failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
