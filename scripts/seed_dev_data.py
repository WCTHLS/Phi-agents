#!/usr/bin/env python3
"""Fill a freshly migrated database with development data.

Usage (from the repository root, after `alembic upgrade head`):
    python scripts/seed_dev_data.py
    python scripts/seed_dev_data.py --database-url postgresql://user:pass@localhost:5432/dbname

It loads the roles, two sample projects and the approved-source register from
data/sources/corpus_manifest.csv. It is safe to run more than once.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pharma_ai.infrastructure.db.seed import DEFAULT_MANIFEST, seed_all
from pharma_ai.infrastructure.db.session import make_engine, make_session_factory


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--database-url", default=None, help="defaults to DATABASE_URL / .env")
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = ap.parse_args()

    engine = make_engine(args.database_url)
    with make_session_factory(engine)() as session:
        counts = seed_all(session, args.manifest)
    print("Seeded:", ", ".join(f"{n} {k}" for k, n in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
