"""Recheck stored provider IDs without rebuilding or deleting diary records."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tennisd import create_app
from tennisd.live_tennis import reconcile_live_matches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help="Maximum detail requests (1–50).")
    args = parser.parse_args()
    if not 1 <= args.limit <= 50:
        parser.error("--limit must be between 1 and 50")
    app = create_app({"AUTO_CREATE_DB": False})
    with app.app_context():
        written = reconcile_live_matches(limit=args.limit, include_finished=True)
        print(f"Provider-confirmed results written: {written}. Existing diary IDs were preserved.")


if __name__ == "__main__":
    main()
