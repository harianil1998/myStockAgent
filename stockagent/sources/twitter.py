"""X / Twitter sub-agent: recent search over watchlist cashtags and followed accounts.

Requires an X API bearer token (X_BEARER_TOKEN). Configure `accounts` with
the traders/analysts you trust; their cashtags become signals.
"""

from __future__ import annotations

import hashlib
import math
import os
from datetime import datetime

from ..models import Signal, SignalKind
from ..sentiment import score_text
from .base import SourceAgent

SEARCH = "https://api.twitter.com/2/tweets/search/recent"


class TwitterAgent(SourceAgent):
    name = "twitter"
    requires_env = ("X_BEARER_TOKEN",)

    def build_queries(self) -> list[str]:
        extra = self.options.get("query_extra", "-is:retweet lang:en")
        queries = []
        accounts = self.options.get("accounts", [])
        # X caps query length at 512 chars; chunk accounts/cashtags.
        for i in range(0, len(accounts), 15):
            chunk = " OR ".join(f"from:{a.lstrip('@')}" for a in accounts[i:i + 15])
            queries.append(f"({chunk}) has:cashtags {extra}")
        for i in range(0, len(self.watchlist), 20):
            chunk = " OR ".join(f"${t}" for t in self.watchlist[i:i + 20])
            queries.append(f"({chunk}) {extra}")
        return queries

    async def fetch(self) -> list[Signal]:
        headers = {"Authorization": f"Bearer {os.environ['X_BEARER_TOKEN']}"}
        out: list[Signal] = []
        async with self.client(headers=headers) as c:
            for q in self.build_queries():
                key = "twitter:since:" + hashlib.sha1(q.encode()).hexdigest()[:16]
                params = {
                    "query": q, "max_results": int(self.options.get("max_results", 100)),
                    "tweet.fields": "created_at,public_metrics,author_id,entities",
                    "expansions": "author_id", "user.fields": "username,public_metrics",
                }
                if since := self.store.get_state(key):
                    params["since_id"] = since
                data = await self.get_json(c, SEARCH, params=params)
                if newest := (data.get("meta") or {}).get("newest_id"):
                    self.store.set_state(key, newest)
                out.extend(self.parse(data))
        return out

    def parse(self, data: dict) -> list[Signal]:
        users = {u["id"]: u for u in (data.get("includes") or {}).get("users", [])}
        out = []
        for tw in data.get("data", []):
            text = tw.get("text", "")
            tags = [c["tag"].upper() for c in (tw.get("entities") or {}).get("cashtags", [])]
            tickers = [t for t in dict.fromkeys(tags or self.universe.extract(text))
                       if not self.universe or t in self.universe]
            if not tickers or len(tickers) > 5:
                continue
            user = users.get(tw.get("author_id"), {})
            followers = (user.get("public_metrics") or {}).get("followers_count", 0)
            m = tw.get("public_metrics") or {}
            weight = 1.0 + math.log10(1 + followers) * 0.4 + math.log1p(m.get("like_count", 0) + 2 * m.get("retweet_count", 0)) * 0.2
            ts = datetime.fromisoformat(tw.get("created_at", "1970-01-01T00:00:00Z").replace("Z", "+00:00"))
            for t in tickers:
                out.append(Signal(
                    source="x", ticker=t, kind=SignalKind.SOCIAL, sentiment=score_text(text), text=text,
                    url=f"https://x.com/{user.get('username', 'i')}/status/{tw.get('id')}",
                    author=user.get("username", ""), timestamp=ts, weight=weight / len(tickers),
                ))
        return out
