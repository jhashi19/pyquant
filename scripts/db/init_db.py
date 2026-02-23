#!/usr/bin/env python3
import argparse
import sqlite3
from pathlib import Path


def _read_sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    default_db = repo_root / "data" / "pyquant.db"
    default_schema = repo_root / "scripts" / "db" / "create_tables.sql"
    default_seed = repo_root / "scripts" / "db" / "seed_master.sql"

    parser = argparse.ArgumentParser(description="Initialize pyquant SQLite database")
    parser.add_argument("--db", default=str(default_db), help="SQLite db path")
    parser.add_argument("--schema", default=str(default_schema), help="Schema SQL path")
    parser.add_argument("--seed", default=str(default_seed), help="Seed SQL path")
    parser.add_argument("--no-seed", action="store_true", help="Skip master seed data")
    args = parser.parse_args()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    schema_path = Path(args.schema)
    seed_path = Path(args.seed)

    if not schema_path.exists():
        raise SystemExit(f"schema not found: {schema_path}")

    if not args.no_seed and not seed_path.exists():
        raise SystemExit(f"seed not found: {seed_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(_read_sql(schema_path))
        if not args.no_seed:
            conn.executescript(_read_sql(seed_path))
        conn.commit()
    finally:
        conn.close()

    print(f"initialized db: {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
