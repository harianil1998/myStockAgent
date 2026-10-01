"""StockTwits sub-agent: trending stream + per-symbol streams for the watchlist.

StockTwits users self-label posts Bullish/Bearish, which is a much better
sentiment signal than any lexicon, so we prefer it when present.
"""

from __future__ import annotations

import math
from datetime import datetime

from ..models import Signal, SignalKind
from ..sentiment import score_text
from .base import SourceAgent

API = "https://api.stocktwits.com/api/2"


class StockTwitsAgent(SourceAgent):
    name = "stocktwits"

    async def fetch(self) -> list[Signal]:
        out: list[Signal] = []
        watch = list(dict.fromkeys(self.watchlist + [t.upper() for t in self.options.get("watchlist", [])]))
        async with self.client() as c:
            out.extend(self.parse(await self.get_json(c, f"{API}/streams/trending.json")))
            try:
                trending = await self.get_json(c, f"{API}/trending/symbols.json")
                self.store.set_state("stocktwits:trending", [s["symbol"] for s in trending.get("symbols", [])])
            except Exception:
                pass
            for sym in watch[:30]:
                out.extend(self.parse(await self.get_json(c, f"{API}/streams/symbol/{sym}.json")))
        return out

    def parse(self, data: dict) -> list[Signal]:
        out = []
        for m in data.get("messages", []):
            body = m.get("body", "")
            symbols = [s.get("symbol", "") for s in m.get("symbols", []) if s.get("symbol")]
            symbols = [s for s in symbols if not s.endswith(".X")]  # drop crypto (BTC.X)
            if not symbols or len(symbols) > 5:
                continue
            label = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
            sentiment = 0.8 if label == "Bullish" else -0.8 if label == "Bearish" else score_text(body)
            user = m.get("user") or {}
            weight = 1.0 + math.log10(1 + user.get("followers", 0)) * 0.3
            ts = datetime.fromisoformat(m.get("created_at", "1970-01-01T00:00:00Z").replace("Z", "+00:00"))
            for sym in symbols:
                out.append(Signal(
                    source="stocktwits", ticker=sym, kind=SignalKind.SOCIAL, sentiment=sentiment,
                    text=body[:500], url=f"https://stocktwits.com/message/{m.get('id')}",
                    author=user.get("username", ""), timestamp=ts, weight=weight / len(symbols),
                    extra={"label": label},
                ))
        return out
