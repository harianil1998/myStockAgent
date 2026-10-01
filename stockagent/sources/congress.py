"""Congress sub-agent: STOCK Act disclosures of US politicians' trades.

Uses Quiver Quantitative's API (QUIVER_API_KEY). Point `url` at any other
JSON feed with the same fields to swap providers.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from ..models import Signal, SignalKind
from .base import SourceAgent

QUIVER = "https://api.quiverquant.com/beta/live/congresstrading"


def range_floor(r: str) -> float:
    nums = re.findall(r"[\d,]+", r or "")
    return float(nums[0].replace(",", "")) if nums else 1000.0


class CongressAgent(SourceAgent):
    name = "congress"
    requires_env = ("QUIVER_API_KEY",)

    async def fetch(self) -> list[Signal]:
        headers = {"Authorization": f"Bearer {os.environ['QUIVER_API_KEY']}", "Accept": "application/json"}
        async with self.client(headers=headers) as c:
            data = await self.get_json(c, self.options.get("url", QUIVER))
        return self.parse(data)

    def parse(self, rows: list[dict]) -> list[Signal]:
        out = []
        for row in rows:
            ticker = (row.get("Ticker") or "").upper()
            tx = (row.get("Transaction") or "").lower()
            if not ticker or not ("purchase" in tx or "sale" in tx):
                continue
            amount = range_floor(row.get("Range", ""))
            date = row.get("ReportDate") or row.get("TransactionDate") or ""
            ts = datetime.fromisoformat(date[:10]).replace(tzinfo=timezone.utc) if date else datetime.now(timezone.utc)
            who = row.get("Representative", "Unknown")
            out.append(Signal(
                source="congress", ticker=ticker, kind=SignalKind.POLITICIAN,
                sentiment=0.7 if "purchase" in tx else -0.4,
                text=f"{who} ({row.get('House', '')}) {row.get('Transaction')} {ticker} {row.get('Range', '')}",
                url="https://www.quiverquant.com/congresstrading/", author=who, timestamp=ts,
                weight=1.0 + min(3.0, amount / 100_000), extra=row,
            ))
        return out
