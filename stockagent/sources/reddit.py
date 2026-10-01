"""Reddit sub-agent: new posts from finance subreddits via the public JSON API."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from ..models import Signal, SignalKind
from ..sentiment import score_text
from .base import SourceAgent


class RedditAgent(SourceAgent):
    name = "reddit"

    async def fetch(self) -> list[Signal]:
        subs = self.options.get("subreddits", ["wallstreetbets"])
        limit = int(self.options.get("limit", 100))
        out: list[Signal] = []
        async with self.client() as c:
            for sub in subs:
                data = await self.get_json(c, f"https://www.reddit.com/r/{sub}/new.json", params={"limit": limit})
                out.extend(self.parse(data, sub))
        return out

    def parse(self, data: dict, sub: str) -> list[Signal]:
        out = []
        for child in data.get("data", {}).get("children", []):
            p = child.get("data", {})
            text = f"{p.get('title', '')}\n{p.get('selftext', '')}".strip()
            tickers = self.universe.extract(text)
            # Posts naming many tickers are usually lists/screeners: cap their influence.
            if not tickers or len(tickers) > 6:
                continue
            sentiment = score_text(text)
            weight = 1.0 + math.log1p(max(0, p.get("score", 0))) * 0.5 + math.log1p(p.get("num_comments", 0)) * 0.3
            ts = datetime.fromtimestamp(p.get("created_utc", 0), tz=timezone.utc)
            for t in tickers:
                out.append(Signal(
                    source=f"reddit/r/{sub}", ticker=t, kind=SignalKind.SOCIAL, sentiment=sentiment,
                    text=text[:600], url="https://www.reddit.com" + p.get("permalink", ""),
                    author=p.get("author", ""), timestamp=ts, weight=weight / len(tickers),
                    extra={"flair": p.get("link_flair_text"), "score": p.get("score", 0)},
                ))
        return out
