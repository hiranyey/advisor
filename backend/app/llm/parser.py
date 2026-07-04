"""NL → structured transactions — the parse half of the data-entry flow.

A tiny structured-output agent turns plain English ("₹50k into HDFC Small Cap, sold
20k of the SBI gold fund last month") into typed rows. It parses ONLY — fund-name
matching, NAV lookup and the confirm/commit step happen back in tools.impl. Keeping the
parse separate from the write is the whole point: a misparse is caught before it lands.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .provider import get_model

_INSTRUCTIONS = """
You extract mutual-fund transactions from an advisor's plain-English note.

Return one row per distinct fund action. For each:
- fund: the fund name/description exactly as the advisor referred to it (e.g. "HDFC Small
  Cap", "SBI gold fund"). Do not invent a full official name — keep it as written.
- type: "buy" for invest/add/SIP/lump-sum in; "redeem" for sell/withdraw/redeem/exit.
- amount: the rupee amount as a number. Expand shorthand: 50k = 50000, 2L = 200000,
  1.5cr = 15000000.
- date: ISO YYYY-MM-DD if a date is stated or implied ("last month", "on 3 June"),
  else null. Resolve relative dates against the provided today's date.

If the note contains no transactions, return an empty list.
""".strip()


class ParsedTxn(BaseModel):
    """One parsed transaction, pre-matching (fund is free text as written)."""

    fund: str = Field(description="fund name/description as the advisor wrote it")
    type: Literal["buy", "redeem"] = "buy"
    amount: float = Field(description="rupee amount as a number")
    date: Optional[str] = Field(default=None, description="ISO date YYYY-MM-DD or null")


@lru_cache(maxsize=1)
def _agent():
    from pydantic_ai import Agent

    return Agent(get_model(), output_type=list[ParsedTxn], instructions=_INSTRUCTIONS)


def _prompt(text: str, today: date | None) -> str:
    today = today or date.today()
    return f"Today's date is {today.isoformat()}.\n\nNote:\n{text}"


async def parse_transactions_text(text: str, today: date | None = None) -> list[dict]:
    """Parse a note into `{fund, type, amount, date}` dicts (async — reuses the caller's
    event loop when invoked from inside the Copilot's tool loop). Never writes."""
    result = await _agent().run(_prompt(text, today))
    return [t.model_dump() for t in result.output]


def parse_transactions_text_sync(text: str, today: date | None = None) -> list[dict]:
    """Sync variant for standalone use (outside a running event loop)."""
    result = _agent().run_sync(_prompt(text, today))
    return [t.model_dump() for t in result.output]
