"""Run every reference query and record its actual result as ground truth.

Expected values written by hand are guesses. This replaces them with what
the warehouse actually returns, so the benchmark scores against reality.
Run this whenever the data is regenerated — a new seed means new answers.
"""

import sys
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "meridian.duckdb"
SPEC = Path(__file__).resolve().parent / "benchmark.yml"


def main() -> None:
    spec = yaml.safe_load(SPEC.read_text())
    con = duckdb.connect(str(DB), read_only=True)

    resolved, skipped, failed = 0, 0, 0

    for q in spec["questions"]:
        sql = q.get("reference_sql")
        if not sql:
            skipped += 1
            print(f"{q['id']}  skip     ({q['expected']})")
            continue

        try:
            rows = con.sql(sql).fetchall()
        except Exception as exc:
            failed += 1
            print(f"{q['id']}  FAILED   {str(exc)[:70]}")
            continue

        resolved += 1
        if len(rows) == 1 and len(rows[0]) == 1:
            value = rows[0][0]
            print(f"{q['id']}  scalar   {value}")
        else:
            print(f"{q['id']}  {len(rows)} rows  first={rows[0] if rows else None}")

    con.close()
    print(f"\nresolved {resolved}, skipped {skipped}, failed {failed}")


if __name__ == "__main__":
    main()
