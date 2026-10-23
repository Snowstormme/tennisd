"""Notify IndexNow search engines about Tennisd's frequently updated entry pages."""

import os
import sys

import requests


HOST = os.environ.get("PUBLIC_HOST", "tennisd.onrender.com").strip().removeprefix("https://").rstrip("/")
KEY = os.environ.get("INDEXNOW_KEY", "a4f23d7e946b4aa8b36740f15f67e2c1").strip()
PATHS = ("/", "/matches", "/players", "/tournaments", "/news")


def main():
    payload = {
        "host": HOST,
        "key": KEY,
        "keyLocation": f"https://{HOST}/{KEY}.txt",
        "urlList": [f"https://{HOST}{path}" for path in PATHS],
    }
    response = requests.post("https://api.indexnow.org/indexnow", json=payload, timeout=20)
    response.raise_for_status()
    print(f"IndexNow accepted {len(PATHS)} URLs (HTTP {response.status_code}).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"IndexNow submission failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
