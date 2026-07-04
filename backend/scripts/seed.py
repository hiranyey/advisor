"""One-shot DB initializer — run this once after deploying to the cloud.

    uv run python -m scripts.seed

It:
  0. creates the database itself if it doesn't exist,
  1. creates all tables + the latest_holdings view (idempotent),
  2. loads funds + full historical NAV series from the two CSVs in scripts/,
  3. tops up NAVs to today from AMFI's live feed, and
  4. generates a deterministic pool of 150 synthetic clients (goals, portfolios, SIPs)
     from the real funds/nav_history.

Safe to re-run: steps 2 and 4 truncate-and-reload, so the result is deterministic
regardless of prior state. Works from truly zero — even a dropped database.
"""

from scripts.gen_clients import generate as generate_clients
from scripts.load_navs import load_from_csvs

from app.db import Base, engine, ensure_database

# Import models so every table is registered on Base.metadata before create_all.
from app import models  # noqa: F401
from app.models import create_views
from app.tasks.refresh_navs import refresh_navs


def create_tables() -> None:
    """Issue CREATE TABLE IF NOT EXISTS for every ORM-mapped table + the views."""
    tables = ", ".join(sorted(Base.metadata.tables))
    print(f"creating tables: {tables}")
    Base.metadata.create_all(engine)
    print("creating views: latest_holdings")
    create_views(engine)


def main() -> None:
    print("== 0/4 · ensuring database exists ==")
    ensure_database()

    print("\n== 1/4 · creating tables + views ==")
    create_tables()

    print("\n== 2/4 · loading funds + historical NAVs from CSVs ==")
    csv_stats = load_from_csvs()

    print("\n== 3/4 · topping up NAVs from AMFI live feed ==")
    try:
        amfi_stats = refresh_navs()
    except Exception as e:  # AMFI down / offline cloud build — CSV load already succeeded
        print(f"  AMFI top-up skipped ({e!r}); CSV NAVs are still loaded.")
        amfi_stats = None

    print("\n== 4/4 · generating synthetic client book ==")
    client_stats = generate_clients()

    print("\n== done ==")
    print(f"  funds: {csv_stats['funds']}")
    print(f"  nav_history rows from CSV: {csv_stats['nav_history']}")
    if amfi_stats:
        print(f"  nav rows refreshed from AMFI: {amfi_stats['rows_upserted']}")
    print(f"  clients: {client_stats.get('clients')}")
    print(f"  goals + holdings + txns + sips: "
          f"{client_stats.get('goal_holdings')} holdings, "
          f"{client_stats.get('transactions')} txns, {client_stats.get('sips')} sips")


if __name__ == "__main__":
    main()
