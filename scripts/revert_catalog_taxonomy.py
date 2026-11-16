"""Revert a logged auxiliary taxonomy change, preserving original POS fields."""
# ruff: noqa: E402

import argparse
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from app.db.repositories.catalog_taxonomy_changes import revert_taxonomy_change
from app.db.session import SessionLocal


def main():
    parser = argparse.ArgumentParser(description="Revertir un cambio de taxonomía auxiliar")
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--change-id", required=True)
    args = parser.parse_args()
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is required")
    with SessionLocal() as session:
        revert_taxonomy_change(session, args.business_id, args.change_id)
        session.commit()
    print("Cambio auxiliar revertido")


if __name__ == "__main__":
    main()
