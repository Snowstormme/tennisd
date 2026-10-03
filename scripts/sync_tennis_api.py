"""Daily recent-result refresh and quota-aware historical backfill."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tennisd import create_app
from tennisd.tennis_api import sync_recent_and_history


def main():
    max_requests = int(os.environ.get("TENNIS_API_DAILY_REQUESTS", "48"))
    app = create_app({"AUTO_CREATE_DB": False})
    with app.app_context():
        result = sync_recent_and_history(max_requests=max_requests)
    print(
        "Tennis API sync used {requests} requests, inspected {seen} results and added {written} matches.".format(
            **result
        )
    )


if __name__ == "__main__":
    main()
