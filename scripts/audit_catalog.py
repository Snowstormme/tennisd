"""Read-only catalog quality report. Never deletes or exports account records."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tennisd import create_app
from tennisd.catalog_audit import catalog_report, readonly_catalog_session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    app = create_app({"AUTO_CREATE_DB": False})
    with app.app_context(), readonly_catalog_session():
        print(json.dumps(catalog_report(args.limit), indent=2))


if __name__ == "__main__":
    main()
