"""Read-only catalog quality report. Never deletes or exports account records."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tennisd import create_app
from tennisd.catalog_audit import catalog_cohort_report, catalog_report, readonly_catalog_session


def audit_year(value):
    if not value:
        return None
    try:
        year = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Year must be an integer.") from None
    if not 1968 <= year <= 2100:
        raise argparse.ArgumentTypeError("Year must be between 1968 and 2100.")
    return year


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cohort", action="store_true", help="Include all public matches in the selected cohort, not just identity conflicts.")
    parser.add_argument("--tour", choices=("", "ATP", "WTA"), default="")
    parser.add_argument("--year", type=audit_year)
    parser.add_argument("--tournament", default="", help="Exact tournament name (case-insensitive).")
    parser.add_argument("--provider", default="")
    parser.add_argument("--after-id", default="", help="Continue samples after this permanent match ID; totals stay cohort-wide.")
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        parser.error("--limit must be between 1 and 500")
    if len(args.tournament.strip()) > 120 or len(args.provider.strip()) > 32 or len(args.after_id.strip()) > 100:
        parser.error("A cohort filter exceeds its field length.")
    app = create_app({"AUTO_CREATE_DB": False})
    with app.app_context(), readonly_catalog_session():
        report = catalog_cohort_report(limit=args.limit, tour=args.tour, year=args.year,
            tournament=args.tournament, provider=args.provider, after_id=args.after_id) if (
            args.cohort or args.tour or args.year or args.tournament or args.provider or args.after_id
        ) else catalog_report(args.limit)
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
