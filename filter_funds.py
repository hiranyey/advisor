#!/usr/bin/env python3
"""Filter mutual funds down to operational Direct Plan – Growth schemes.

Keeps only rows that satisfy all of:
  1. Still operational        -> Open Ended scheme with no real closure date.
  2. No closure date          -> Closure_Date is blank, OR equal to Launch_Date
                                 (the latter is a data artifact, not a real closure).
  3. Direct Plan              -> Scheme_NAV_Name mentions "Direct".
  4. Growth (no dividend)     -> Scheme_NAV_Name mentions "Growth" and has no
                                 dividend markers (IDCW / Dividend / Div / Payout /
                                 Reinvest / Bonus).

Usage:
    python3 filter_funds.py [input.csv] [output.csv]
"""

import csv
import sys

INPUT = sys.argv[1] if len(sys.argv) > 1 else "mutual_fund_data.csv"
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else "direct_growth_operational_funds.csv"

# Words in the NAV name that signal a dividend / income-distribution plan.
DIVIDEND_MARKERS = ("idcw", "dividend", "div ", "-div", "payout", "reinvest", "bonus")


def is_operational(row):
    """Open-ended fund with no real closure date."""
    if row["Scheme_Type"].strip().lower() != "open ended":
        return False
    closure = row["Closure_Date"].strip()
    launch = row["Launch_Date"].strip()
    # Blank closure date, or the artifact where closure == launch, counts as "open".
    return closure == "" or closure == launch


def is_direct_growth_no_dividend(row):
    name = row["Scheme_NAV_Name"].strip().lower()
    if "direct" not in name or "growth" not in name:
        return False
    return not any(marker in name for marker in DIVIDEND_MARKERS)


def main():
    with open(INPUT, newline="", encoding="utf-8") as fin:
        reader = csv.DictReader(fin)
        fieldnames = reader.fieldnames
        kept = [
            row
            for row in reader
            if is_operational(row) and is_direct_growth_no_dividend(row)
        ]

    with open(OUTPUT, "w", newline="", encoding="utf-8") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)

    print(f"Read   : {INPUT}")
    print(f"Kept   : {len(kept)} funds")
    print(f"Written: {OUTPUT}")


if __name__ == "__main__":
    main()
