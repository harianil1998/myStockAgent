"""Market-data sub-agent: price/volume anomalies via Yahoo Finance's chart API.

Also used by the orchestrator to enrich candidates with momentum context
before they reach the analyst.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from ..models import Signal, SignalKind
from .base import DEFAULT_UA, SourceAgent

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{t}"


def compute_stats(chart: dict) -> dict | None:
    try:
        res = chart["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        return None
    closes = [c for c in q.get("close", []) if c is not None]
    vols = [v for v in q.get("volume", []) if v is not None]
    if len(closes) < 6 or len(vols) < 6:
        return None
    last, prev = closes[-1], closes[-2]
    base_vol = sum(vols[-21:-1]) / max(1, len(vols[-21:-1]))
    return {
        "price": round(last, 2),
        "chg_1d": round((last / prev - 1) * 100, 2),
        "chg_5d": round((last / closes[-6] - 1) * 100, 2),
        "chg_1m": round((last / closes[-22] - 1) * 100, 2) if len(closes) >= 22 else None,
        "vol_ratio": round(vols[-1] / base_vol, 2) if base_vol else None,
        "high_3m": round(max(closes), 2),
        "low_3m": round(min(closes), 2),
    }


async def fetch_stats(tickers: list[str], concurrency: int = 8) -> dict[str, dict]:
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": DEFAULT_UA}) as c:
        async def one(t: str):
            async with sem:
                try:
                    r = await c.get(CHART.format(t=t), params={"range": "3mo", "interval": "1d"})
                    r.raise_for_status()
                    if s := compute_stats(r.json()):
                        out[t] = s
                except Exception:
                    pass
        await asyncio.gather(*(one(t) for t in tickers))
    return out


class MarketAgent(SourceAgent):
    name = "market"

    async def fetch(self) -> list[Signal]:
        # Watchlist + whatever is currently trending on StockTwits.
        tickers = list(dict.fromkeys(self.watchlist + (self.store.get_state("stocktwits:trending") or [])))[:60]
        return self.to_signals(await fetch_stats(tickers))

    def to_signals(self, stats: dict[str, dict]) -> list[Signal]:
        out = []
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        for t, s in stats.items():
            unusual_vol = (s.get("vol_ratio") or 0) >= 2.0
            big_move = abs(s["chg_1d"]) >= 5
            if not (unusual_vol or big_move):
                continue
            direction = 1 if s["chg_1d"] >= 0 else -1
            out.append(Signal(
                source="market", ticker=t, kind=SignalKind.MARKET,
                sentiment=direction * min(1.0, abs(s["chg_1d"]) / 10 + (0.3 if unusual_vol else 0)),
                text=f"{t} {s['chg_1d']:+.1f}% today on {s.get('vol_ratio')}x avg volume (5d {s['chg_5d']:+.1f}%)",
                url=f"https://finance.yahoo.com/quote/{t}", timestamp=now,
                weight=1.0 + (s.get("vol_ratio") or 1) * 0.5, extra=s,
                id=f"market|{t}|{today}",  # at most one anomaly signal per ticker per day
            ))
        return out
