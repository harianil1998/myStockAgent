"""ARK Invest sub-agent: ARK publishes full ETF holdings daily, so diffing
consecutive snapshots reveals Cathie Wood's buys and sells each day."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from ..models import Signal, SignalKind
from .base import SourceAgent

BASE = "https://assets.ark-funds.com/fund-documents/funds-etf-csv/"
FUND_FILES = {
    "ARKK": "ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv",
    "ARKW": "ARK_NEXT_GENERATION_INTERNET_ETF_ARKW_HOLDINGS.csv",
    "ARKG": "ARK_GENOMIC_REVOLUTION_ETF_ARKG_HOLDINGS.csv",
    "ARKQ": "ARK_AUTONOMOUS_TECH._&_ROBOTICS_ETF_ARKQ_HOLDINGS.csv",
    "ARKF": "ARK_FINTECH_INNOVATION_ETF_ARKF_HOLDINGS.csv",
    "ARKX": "ARK_SPACE_EXPLORATION_&_INNOVATION_ETF_ARKX_HOLDINGS.csv",
}
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def parse_holdings(text: str) -> dict[str, dict]:
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        t = row.get("ticker", "").split(" ")[0].upper()
        if not t or not row.get("shares"):
            continue
        out[t] = {
            "shares": float(row["shares"].replace(",", "")),
            "weight": float(row.get("weight (%)", "0").replace("%", "") or 0),
            "company": row.get("company", ""),
            "date": row.get("date", ""),
        }
    return out


class ArkAgent(SourceAgent):
    name = "ark"

    async def fetch(self) -> list[Signal]:
        urls = {f: BASE + FUND_FILES[f] for f in self.options.get("funds", FUND_FILES) if f in FUND_FILES}
        urls.update(self.options.get("urls", {}))
        out: list[Signal] = []
        async with self.client(headers={"User-Agent": BROWSER_UA}) as c:
            for fund, url in urls.items():
                r = await c.get(url)
                r.raise_for_status()
                cur = parse_holdings(r.text)
                key = f"ark:{fund}"
                prev = self.store.get_state(key)
                if prev and prev.get("_date") != _date(cur):
                    out.extend(self.diff(fund, prev, cur))
                if cur:
                    self.store.set_state(key, {**cur, "_date": _date(cur)})
        return out

    def diff(self, fund: str, prev: dict, cur: dict, min_change: float = 0.03) -> list[Signal]:
        out = []
        now = datetime.now(timezone.utc)
        for t, h in cur.items():
            p = prev.get(t)
            if p is None:
                action, change, sentiment = "NEW", 1.0, 0.9
            else:
                change = (h["shares"] - p["shares"]) / p["shares"] if p["shares"] else 0
                if abs(change) < min_change:
                    continue
                action, sentiment = ("BUY", 0.6) if change > 0 else ("SELL", -0.5)
            out.append(self._sig(fund, t, action, change, sentiment, h.get("weight", 0), now))
        for t, p in prev.items():
            if t != "_date" and t not in cur:
                out.append(self._sig(fund, t, "EXIT", -1.0, -0.8, 0, now))
        return out

    def _sig(self, fund, ticker, action, change, sentiment, weight_pct, ts) -> Signal:
        return Signal(
            source=f"ark/{fund}", ticker=ticker, kind=SignalKind.SMART_MONEY, sentiment=sentiment,
            text=f"ARK {fund}: {action} {ticker} ({change:+.1%} shares, now {weight_pct:.2f}% of fund)",
            url="https://ark-funds.com/", author="ARK Invest", timestamp=ts,
            weight=2.0 + weight_pct * 0.3, extra={"action": action, "change": change},
        )


def _date(holdings: dict) -> str:
    return next((h["date"] for h in holdings.values() if isinstance(h, dict) and h.get("date")), "")
