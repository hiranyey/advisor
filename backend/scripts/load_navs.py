"""Seed `funds` and `nav_history` from the two CSVs in this scripts/ folder.

  - direct_growth_categorized.csv              -> funds (name, amc, scheme_code, category)
  - direct_growth_no_corp_action_nav_history.csv -> nav_history (fund_id, date, nav)

NAV history (~1.8M rows) is bulk-loaded via Postgres COPY into a scheme_code-keyed
staging table, then joined onto funds — orders of magnitude faster than ORM inserts.

Run from backend/:  uv run python -m scripts.load_navs
"""

import csv
import sys
from pathlib import Path

from sim_kernel.categories import CATEGORIES

from app.db import Base, engine
from app.models import Fund, NavHistory  # noqa: F401 — register tables on Base

DATA_DIR = Path(__file__).resolve().parent  # CSVs live alongside this script
FUNDS_CSV = DATA_DIR / "direct_growth_categorized.csv"
NAV_CSV = DATA_DIR / "direct_growth_no_corp_action_nav_history.csv"

VALID = set(CATEGORIES)


def load_funds(cur) -> int:
    """Insert one row per fund from the categorized CSV. Idempotent via unique
    scheme_code (ON CONFLICT updates name/category so re-runs stay current)."""
    rows = []
    skipped = 0
    with FUNDS_CSV.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = (r["Scheme_Code"] or "").strip()
            cat = (r["Internal_Category"] or "").strip()
            if not code or cat not in VALID:
                skipped += 1
                continue
            name = (r["Scheme_Name"] or "").strip()
            amc = name.split(" ", 1)[0] if name else None  # best-effort fund house
            rows.append((name, amc, code, cat))

    cur.executemany(
        """
        insert into funds (name, amc, scheme_code, category)
        values (%s, %s, %s, %s)
        on conflict (scheme_code) do update
          set name = excluded.name,
              amc = excluded.amc,
              category = excluded.category
        """,
        rows,
    )
    if skipped:
        print(f"  funds: skipped {skipped} rows (bad code/category)")
    return len(rows)


def load_navs(cur) -> int:
    """COPY the NAV dump into a staging table, then join onto funds by scheme_code."""
    cur.execute(
        """
        create temp table _nav_stage (
          scheme_code text,
          date        date,
          nav         numeric
        ) on commit drop
        """
    )
    with NAV_CSV.open("rb") as f:
        with cur.copy(
            "copy _nav_stage (scheme_code, date, nav) "
            "from stdin with (format csv, header true)"
        ) as copy:
            while chunk := f.read(1 << 20):
                copy.write(chunk)

    cur.execute(
        """
        insert into nav_history (fund_id, date, nav)
        select f.id, s.date, s.nav
        from _nav_stage s
        join funds f on f.scheme_code = s.scheme_code
        where s.nav is not null
        on conflict (fund_id, date) do nothing
        """
    )
    return cur.rowcount


def load_from_csvs() -> dict:
    """(Re)load funds + nav_history from the scripts/ CSVs. Assumes tables already exist.
    Returns stats."""
    for p in (FUNDS_CSV, NAV_CSV):
        if not p.exists():
            sys.exit(f"missing CSV: {p}")

    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            print("clearing existing fund/nav data ...")
            cur.execute("truncate nav_history, funds restart identity cascade")

            n_funds = load_funds(cur)
            print(f"  funds inserted: {n_funds}")

            print("bulk-loading nav_history via COPY (this takes a moment) ...")
            n_navs = load_navs(cur)
            print(f"  nav_history inserted: {n_navs}")
        raw.commit()
    finally:
        raw.close()

    print("done.")
    return {"funds": n_funds, "nav_history": n_navs}


if __name__ == "__main__":
    Base.metadata.create_all(engine)  # standalone run: ensure tables exist first
    load_from_csvs()
