"""Scheduled live score import. Run inside an application context."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tennisd import create_app
from tennisd.live_tennis import sync_matches


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    if mode == "auto":
        # Midnight UTC refreshes the next seven days; other runs refresh live scores.
        mode = "upcoming" if datetime.now(timezone.utc).hour == 0 else "live"
    app = create_app({"AUTO_CREATE_DB": False})
    with app.app_context():
        count = sync_matches(mode)
    print(f"Synced {count} {mode} ATP, WTA, Challenger and ITF matches.")


if __name__ == "__main__":
    main()
