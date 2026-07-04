#!/usr/bin/env python3
"""Tag each fund in direct_growth_no_corp_action.csv with one of the 14
internal categories defined in cache/HACKATHON.md.

Strategy:
  * Most funds map straight from the AMFI `Scheme_Category` string.
  * The catch-all AMFI buckets ("Index Funds", "FoF Domestic", "FoF Overseas")
    and the legacy labels (Income / Growth / Gilt / ELSS / Liquid) mix asset
    classes, so those are split by reading the fund NAME/underlying:
      - equity index -> risk bucket by the index it tracks
        (sectoral/thematic & small-cap -> high, mid-cap/multi/flexi -> mid,
         broad/large-cap -> low)
      - debt index (SDL / Gilt / G-Sec / IBX / Bond / target-maturity) -> good_debt
      - gold / silver / gold+silver -> gold / silver / gold
      - overseas -> international_equity (US treasury bond FoFs -> good_debt)

The 14 tags (see HACKATHON.md):
  high_risk_equity mid_risk_equity low_risk_equity international_equity
  cash_equivalent good_debt bad_debt gold silver
  aggressive_hybrid balanced_advantage conservative_hybrid multi_asset other
"""

import csv
import sys

INPUT = sys.argv[1] if len(sys.argv) > 1 else "direct_growth_no_corp_action.csv"
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else "direct_growth_categorized.csv"

# ---- direct AMFI sub-category -> internal tag -----------------------------
DIRECT = {
    "Equity Scheme - Small Cap Fund": "high_risk_equity",
    "Equity Scheme - Sectoral/ Thematic": "high_risk_equity",
    "Equity Scheme - Mid Cap Fund": "mid_risk_equity",
    "Equity Scheme - Large & Mid Cap Fund": "mid_risk_equity",
    "Equity Scheme - Multi Cap Fund": "mid_risk_equity",
    "Equity Scheme - Flexi Cap Fund": "mid_risk_equity",
    "Equity Scheme - Value Fund": "mid_risk_equity",
    "Equity Scheme - Focused Fund": "mid_risk_equity",
    "Equity Scheme - Contra Fund": "mid_risk_equity",
    "Equity Scheme - ELSS": "mid_risk_equity",
    "Equity Scheme - Large Cap Fund": "low_risk_equity",
    "Debt Scheme - Overnight Fund": "cash_equivalent",
    "Debt Scheme - Liquid Fund": "cash_equivalent",
    "Debt Scheme - Money Market Fund": "cash_equivalent",
    "Debt Scheme - Ultra Short Duration Fund": "cash_equivalent",
    "Hybrid Scheme - Arbitrage Fund": "cash_equivalent",
    "Debt Scheme - Gilt Fund": "good_debt",
    "Debt Scheme - Gilt Fund with 10 year constant duration": "good_debt",
    "Debt Scheme - Corporate Bond Fund": "good_debt",
    "Debt Scheme - Banking and PSU Fund": "good_debt",
    "Debt Scheme - Short Duration Fund": "good_debt",
    "Debt Scheme - Low Duration Fund": "good_debt",
    "Debt Scheme - Medium Duration Fund": "good_debt",
    "Debt Scheme - Medium to Long Duration Fund": "good_debt",
    "Debt Scheme - Long Duration Fund": "good_debt",
    "Debt Scheme - Dynamic Bond": "good_debt",
    "Debt Scheme - Floater Fund": "good_debt",
    "Debt Scheme - Credit Risk Fund": "bad_debt",
    "Hybrid Scheme - Aggressive Hybrid Fund": "aggressive_hybrid",
    "Hybrid Scheme - Equity Savings": "aggressive_hybrid",
    "Hybrid Scheme - Balanced Hybrid Fund": "aggressive_hybrid",
    "Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage": "balanced_advantage",
    "Hybrid Scheme - Conservative Hybrid Fund": "conservative_hybrid",
    "Hybrid Scheme - Multi Asset Allocation": "multi_asset",
    "Solution Oriented Scheme - Retirement Fund": "other",
    "Solution Oriented Scheme - Children s Fund": "other",
}

# ---- name keyword lists ----------------------------------------------------
# Debt underlyings (target-maturity index funds, bond/gilt FoFs).
DEBT_MARKERS = (
    "sdl", "gilt", "g-sec", "g sec", "gsec", "ibx", "bond", "duration",
    "treasury", "crisil", "maturity", "psu bond",
)
# Sectoral / thematic equity -> highest risk.
SECTORAL = (
    "bank", "pharma", "auto", "healthcare", "realty", "metal", "defence",
    "defense", "infrastructure", "manufacturing", "consumption", "consumer",
    "internet", "digital", "housing", "tourism", "capital market", "commodit",
    "financial services", "private bank", "fmcg", "energy", "nifty it",
    "it index", "it and telecom", "railway", "non-cyclical", "ipo ", "esg",
    "hospital", "fang", "artificial intelligence", "sector leaders",
    "business group", "select business", "psu bank", "psu index", "new age",
    "electric", "autonomous", "aqua", "reit", "tech",
)
SMALLCAP = ("smallcap", "small cap", "midsmall", "mid small", "midsmallcap")
MIDCAP = ("midcap", "mid cap")
MULTI_EQUITY = (
    "multicap", "multi cap", "flexicap", "flexi cap", "all cap", "allcap",
    "largemidcap", "large & mid", "large and mid", "largemid", "large mid",
)


def classify_equity_index(name):
    """Equity index/ETF -> risk bucket from the index it tracks."""
    if any(k in name for k in SECTORAL):
        return "high_risk_equity"
    if any(k in name for k in SMALLCAP):
        return "high_risk_equity"
    if any(k in name for k in MIDCAP):
        return "mid_risk_equity"
    if any(k in name for k in MULTI_EQUITY):
        return "mid_risk_equity"
    return "low_risk_equity"  # broad / large-cap / factor on broad universe


def classify_index_fund(name):
    """AMFI 'Index Funds' bucket: debt index -> good_debt, else equity."""
    if any(k in name for k in DEBT_MARKERS):
        return "good_debt"
    return classify_equity_index(name)


# Overseas markers on funds AMFI happens to file under "FoF Domestic".
INTL_MARKERS = ("nyse", "nasdaq", "hang seng", "s&p 500", "fang", "global",
                "overseas")


def classify_fof_domestic(name):
    has_gold = "gold" in name
    has_silver = "silver" in name
    if has_gold and has_silver:
        return "gold"          # gold+silver precious-metal basket
    if has_silver:
        return "silver"
    if has_gold:
        return "gold"
    if any(k in name for k in INTL_MARKERS):
        return "international_equity"  # US/China/global equity feeders
    # Normalize separators so "Multi - Asset" / "Multi-Asset" both match.
    flat = " ".join(name.replace("-", " ").split())
    if "multi asset" in flat:
        return "multi_asset"
    if "arbitrage" in name:    # "Income Plus Arbitrage" debt+market-neutral
        return "good_debt"
    if any(k in name for k in DEBT_MARKERS) or "debt" in name:
        return "good_debt"
    if "dynamic asset allocation" in name or "balanced advantage" in name:
        return "balanced_advantage"
    if "aggressive hybrid" in name:
        return "aggressive_hybrid"
    if "conservative hybrid" in name:
        return "conservative_hybrid"
    return classify_equity_index(name)  # equity ETF fund-of-funds


def classify_fof_overseas(name):
    if "treasury" in name or ("bond" in name and "equity" not in name):
        return "good_debt"
    return "international_equity"


def classify_legacy(category, name):
    """Old-style labels without the SEBI prefix."""
    if category in ("Liquid",):
        return "cash_equivalent"
    if category in ("Gilt",):
        return "good_debt"
    if category in ("ELSS",):
        return "mid_risk_equity"
    if category == "Income":
        if "mip" in name:
            return "conservative_hybrid"
        if "blended" in name:
            return "cash_equivalent"
        return "good_debt"          # interval / income debt funds
    if category == "Growth":
        if "blended" in name:
            return "cash_equivalent"
        if "infrastructure" in name:
            return "high_risk_equity"
        if any(k in name for k in MIDCAP):
            return "mid_risk_equity"
        if "large cap" in name:
            return "low_risk_equity"
        return "mid_risk_equity"    # generic equity growth
    return "other"


def categorize(row):
    category = row["Scheme_Category"].strip()
    name = row["Scheme_Name"].strip().lower()
    if category in DIRECT:
        return DIRECT[category]
    if category == "Other Scheme - Index Funds":
        return classify_index_fund(name)
    if category == "Other Scheme - FoF Domestic":
        return classify_fof_domestic(name)
    if category == "Other Scheme - FoF Overseas":
        return classify_fof_overseas(name)
    if category in ("Income", "Growth", "Gilt", "ELSS", "Liquid"):
        return classify_legacy(category, name)
    return "other"


def main():
    with open(INPUT, newline="", encoding="utf-8") as fin:
        reader = csv.DictReader(fin)
        rows = list(reader)
        fieldnames = reader.fieldnames + ["Internal_Category"]

    from collections import Counter
    dist = Counter()
    for r in rows:
        r["Internal_Category"] = categorize(r)
        dist[r["Internal_Category"]] += 1

    with open(OUTPUT, "w", newline="", encoding="utf-8") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Read  : {INPUT} ({len(rows)} funds)")
    print(f"Written: {OUTPUT}\n")
    print("Distribution across the 14 internal categories:")
    for cat, n in dist.most_common():
        print(f"  {n:5d}  {cat}")
    total_cats = 14
    print(f"\nCategories used: {len(dist)}/{total_cats}")


if __name__ == "__main__":
    main()
