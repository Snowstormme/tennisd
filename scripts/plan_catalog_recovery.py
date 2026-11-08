"""Preview source-backed catalog corrections. This command cannot apply changes."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tennisd import create_app
from tennisd.catalog_audit import readonly_catalog_session
from tennisd.catalog_recovery import recovery_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help="Maximum matches and provider requests (1–50).")
    parser.add_argument("--match-id", action="append", default=[], help="Restrict the preview to these permanent match IDs.")
    args = parser.parse_args()
    if not 1 <= args.limit <= 50:
        parser.error("--limit must be between 1 and 50")
    app = create_app({"AUTO_CREATE_DB": False})
    with app.app_context(), readonly_catalog_session():
        print(json.dumps(recovery_plan(limit=args.limit, match_ids=args.match_id), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
