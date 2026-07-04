#!/usr/bin/env python3
"""Remove funds that show a bonus/split-style corporate action.

A bonus-unit or unit-split event makes the NAV drop sharply in a single step
(the per-unit value halves or worse while total investment value is unchanged)
and then STAYS at the new level. We flag any scheme whose sorted daily NAV
series has a day-over-day drop of >= DROP_THRESHOLD that persists (the next
NAV does not rebound to the pre-drop level), and filter those funds OUT of the
operational Direct-Growth list.

The persistence check is what separates a real corporate action from a one-day
bad-data spike (e.g. NAV 11.86 -> 0.19 -> 11.99), which recovers immediately
and must NOT be excluded.

Inputs:
    direct_growth_operational_funds.csv   (from filter_funds.py)
    mutual_fund_nav_history.parquet        (index: Scheme_Code; cols: Date, NAV)

Outputs:
    direct_growth_no_corp_action.csv       kept funds (no big NAV drop)
    excluded_corporate_actions.csv         removed funds + drop details

Usage:
    python3 filter_corporate_actions.py
"""

import pandas as pd

FUNDS_CSV = "direct_growth_operational_funds.csv"
NAV_PARQUET = "mutual_fund_nav_history.parquet"
KEPT_CSV = "direct_growth_no_corp_action.csv"
EXCLUDED_CSV = "excluded_corporate_actions.csv"

DROP_THRESHOLD = -0.50  # a single-day fall of 50% or more
# A genuine split stays down; require the next NAV to remain at least this far
# below the pre-drop NAV, otherwise it's a transient bad-data spike.
PERSIST_MAX_RATIO = 0.60  # next NAV must be <= 60% of the pre-drop NAV


def main():
    funds = pd.read_csv(FUNDS_CSV)
    fund_codes = set(funds["Scheme_Code"].astype(int))

    nav = pd.read_parquet(NAV_PARQUET).reset_index()
    nav["Scheme_Code"] = nav["Scheme_Code"].astype(int)
    # Only the funds we care about, and only usable NAVs.
    nav = nav[nav["Scheme_Code"].isin(fund_codes)]
    nav = nav[nav["NAV"] > 0].sort_values(["Scheme_Code", "Date"])

    grp = nav.groupby("Scheme_Code")["NAV"]
    # Day-over-day % change, plus the NAV before and after each point.
    nav["pct_change"] = grp.pct_change()
    nav["prev_nav"] = grp.shift(1)
    nav["next_nav"] = grp.shift(-1)

    drops = nav[nav["pct_change"] <= DROP_THRESHOLD].copy()
    # Persistence: the NAV after the drop must stay down (real split), not
    # rebound (bad-data spike). If there's no next NAV, treat the drop as real.
    persisted = drops["next_nav"].isna() | (
        drops["next_nav"] <= drops["prev_nav"] * PERSIST_MAX_RATIO
    )
    drops = drops[persisted]

    # For each flagged scheme keep its single worst drop for the report.
    worst = (
        drops.sort_values("pct_change")
        .groupby("Scheme_Code")
        .first()
        .reset_index()
    )
    flagged_codes = set(worst["Scheme_Code"])

    kept = funds[~funds["Scheme_Code"].isin(flagged_codes)]
    excluded = funds[funds["Scheme_Code"].isin(flagged_codes)].merge(
        worst[["Scheme_Code", "Date", "NAV", "pct_change"]].rename(
            columns={
                "Date": "Drop_Date",
                "NAV": "NAV_After_Drop",
                "pct_change": "Drop_Pct",
            }
        ),
        on="Scheme_Code",
        how="left",
    )
    excluded["Drop_Pct"] = (excluded["Drop_Pct"] * 100).round(2)

    kept.to_csv(KEPT_CSV, index=False)
    excluded.to_csv(EXCLUDED_CSV, index=False)

    print(f"Input funds        : {len(funds)}")
    print(f"Flagged (>=50% drop): {len(flagged_codes)}")
    print(f"Kept               : {len(kept)}  -> {KEPT_CSV}")
    print(f"Excluded           : {len(excluded)}  -> {EXCLUDED_CSV}")


if __name__ == "__main__":
    main()
