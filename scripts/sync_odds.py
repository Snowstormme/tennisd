"""Scheduled tennis odds refresh."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tennisd import create_app
from tennisd.odds import sync_odds


if __name__ == "__main__":
    app = create_app({"AUTO_CREATE_DB": False})
    with app.app_context():
        result = sync_odds()
    print("Odds sync: {written} matches from {sport} ({events} events checked).".format(**result))
