"""Preview, apply or roll back one version-controlled, source-reviewed result."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tennisd import create_app, db
from tennisd.catalog_audit import readonly_catalog_session
from tennisd.result_corrections import batches, definitions, reviewed_batch, reviewed_correction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("correction", choices=tuple(definitions()) + tuple(batches()))
    parser.add_argument("--operation", choices=("preview", "apply", "rollback"), default="preview")
    args = parser.parse_args()
    app = create_app({"AUTO_CREATE_DB": False})
    operate = reviewed_batch if args.correction in batches() else reviewed_correction
    with app.app_context():
        if args.operation == "preview":
            with readonly_catalog_session():
                result = operate(args.correction)
        else:
            try:
                result = operate(args.correction, operation=args.operation)
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
