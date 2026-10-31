"""Read-only catalog quality report. Never deletes or exports account records."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select
from tennisd import create_app, db
from tennisd.identities import catalog_identity_is_valid
from tennisd.models import Match, Player


def catalog_report(limit=50):
    invalid = ~catalog_identity_is_valid(include_status=False)
    null_points = Match.score.ilike("%None%") | Match.score.ilike("%null%")
    return {
        "cross_tour_or_self_matches": db.session.scalar(select(func.count(Match.id)).where(invalid)),
        "invalid_match_ids": list(db.session.scalars(
            select(Match.id).where(invalid).order_by(Match.id).limit(limit)
        )),
        "legacy_null_scores": db.session.scalar(select(func.count(Match.id)).where(null_points)),
        "held_or_cancelled_results": db.session.scalar(select(func.count(Match.id)).where(Match.status != "finished")),
        "placeholder_players": db.session.scalar(select(func.count(Player.id)).where(
            func.lower(Player.name).in_(("unknown player", "unknown", "tbd", "tba")),
        )),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    app = create_app({"AUTO_CREATE_DB": False})
    with app.app_context():
        print(json.dumps(catalog_report(max(1, min(args.limit, 500))), indent=2))


if __name__ == "__main__":
    main()
