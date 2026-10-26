"""One-time, quota-conscious backfill from Live Tennis API history.

Usage::

    python scripts/import_live_history.py 2026-10-19 2026-10-26

The endpoint is History-gated. A FREE key has a limited monthly taste, so this
script makes one request for the requested window and never pages implicitly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tennisd import create_app
from tennisd.live_tennis import import_history_matches


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: import_live_history.py YYYY-MM-DD YYYY-MM-DD")
    from_date, to_date = sys.argv[1:]
    app = create_app({"AUTO_CREATE_DB": False})
    with app.app_context():
        seen, written = import_history_matches(from_date, to_date)
        print(f"History rows accepted: {seen}; permanent matches written: {written}.")


if __name__ == "__main__":
    main()
