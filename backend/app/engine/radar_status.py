"""Single definition of "what counts as a breach" — was duplicated across book.py's KPI
loop and tools/impl.py's rank_book. Anything that buckets a client by suitability_mismatch
should call this instead of re-deriving the threshold.
"""

from __future__ import annotations

WATCH_BAND = 0.05  # within this much of tolerance (and under it) = "watch"


def status(mismatch: float | None) -> str:
    """'breach' (over tolerance), 'watch' (close to it), or 'ok'."""
    m = mismatch or 0.0
    if m > 0:
        return "breach"
    if m > -WATCH_BAND:
        return "watch"
    return "ok"
