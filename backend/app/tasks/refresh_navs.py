"""Daily NAV refresh from AMFI's public NAVAll dump.

Fetches https://portal.amfiindia.com/spages/NAVAll.txt, parses the semicolon-
delimited rows, and upserts today's NAV into `nav_history` for every scheme_code
we already track in `funds`. Schemes we don't hold are ignored — we never add funds
here, only refresh the ones the DB knows about.

The file is a mix of section headers (no ';'), AMC names (no ';'), blank lines, and
data rows:  code;isin1;isin2;name;nav;DD-Mon-YYYY  (NAV may be 'N.A.').

Sync on purpose so it runs cleanly on APScheduler's threadpool and reuses the sync
DB engine. Returns a small stats dict for logging / the manual-trigger endpoint.
"""

from datetime import date, datetime

import httpx

from app.db import engine

AMFI_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"


def _parse(text: str) -> dict[str, tuple[date, float]]:
    """scheme_code -> (nav_date, nav) for every valid data row in the dump."""
    out: dict[str, tuple[date, float]] = {}
    for line in text.splitlines():
        parts = line.split(";")
        if len(parts) < 6:
            continue
        code = parts[0].strip()
        if not code.isdigit():  # skips the header row and any stray text
            continue
        nav_raw = parts[4].strip()
        date_raw = parts[5].strip()
        if not nav_raw or nav_raw.upper() in ("N.A.", "NA", "-"):
            continue
        try:
            nav = float(nav_raw)
            nav_date = datetime.strptime(date_raw, "%d-%b-%Y").date()
        except ValueError:
            continue
        out[code] = (nav_date, nav)
    return out


def refresh_navs(timeout: float = 60.0) -> dict:
    """Fetch AMFI NAVAll, upsert latest NAVs for known funds. Returns stats."""
    resp = httpx.get(AMFI_URL, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    parsed = _parse(resp.text)

    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(
                "select scheme_code, id from funds where scheme_code is not null"
            )
            code_to_id = {code: fid for code, fid in cur.fetchall()}

            rows = [
                (code_to_id[code], nav_date, nav)
                for code, (nav_date, nav) in parsed.items()
                if code in code_to_id
            ]
            if rows:
                cur.executemany(
                    """
                    insert into nav_history (fund_id, date, nav)
                    values (%s, %s, %s)
                    on conflict (fund_id, date) do update set nav = excluded.nav
                    """,
                    rows,
                )
        raw.commit()
    finally:
        raw.close()

    stats = {
        "parsed_schemes": len(parsed),
        "known_funds": len(code_to_id),
        "rows_upserted": len(rows),
    }
    print(f"[refresh_navs] {stats}")
    return stats


if __name__ == "__main__":
    refresh_navs()
