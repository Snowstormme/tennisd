"""Read-only tournament-name collision inventory; no account data or mutations."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tennisd import create_app
from tennisd.catalog_audit import readonly_catalog_session
from tennisd.tournament_name_audit import tournament_name_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=50, help='Maximum collision groups (1–500).')
    parser.add_argument('--include-inventory', action='store_true', help='Include all aggregated visible tour/name pairs, not match rows.')
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        parser.error('--limit must be between 1 and 500')
    app = create_app({'AUTO_CREATE_DB': False})
    with app.app_context(), readonly_catalog_session():
        print(json.dumps(tournament_name_report(args.limit, include_inventory=args.include_inventory), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
