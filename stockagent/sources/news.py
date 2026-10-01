"""News sub-agent: financial RSS feeds + per-ticker Yahoo Finance headlines."""

from __future__ import annotations

import calendar
from datetime import datetime, timezone

import feedparser

from ..models import Signal, SignalKind
from ..sentiment import score_text
from .base import SourceAgent

YAHOO_TICKER_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US"


class NewsAgent(SourceAgent):
    name = "news"

    async def fetch(self) -> list[Signal]:
        out: list[Signal] = []
        feeds = [(u, None) for u in self.options.get("feeds", [])]
        feeds += [(YAHOO_TICKER_RSS.format(t=t), t) for t in self.watchlist[:30]]
        errors = []
        async with self.client() as c:
            for url, ticker in feeds:
                try:
                    r = await c.get(url)
                    r.raise_for_status()
                except Exception as e:  # one dead feed shouldn't drop the others
                    errors.append(f"{url}: {e}")
                    continue
                out.extend(self.parse(r.text, url, ticker))
        if feeds and len(errors) == len(feeds):
            raise RuntimeError(f"all {len(feeds)} feeds failed, e.g. {errors[0]}")
        return out

    def parse(self, xml: str, feed_url: str, ticker: str | None = None) -> list[Signal]:
        feed = feedparser.parse(xml)
        host = feed_url.split("/")[2] if "//" in feed_url else feed_url
        out = []
        for e in feed.entries:
            title = e.get("title", "")
            summary = e.get("summary", "")
            text = f"{title}. {summary}"
            tickers = [ticker] if ticker else self.universe.extract(text)
            if not tickers or len(tickers) > 4:
                continue
            published = e.get("published_parsed") or e.get("updated_parsed")
            ts = datetime.fromtimestamp(calendar.timegm(published), tz=timezone.utc) if published else datetime.now(timezone.utc)
            # Headlines carry the signal; weight title sentiment higher than the summary.
            sentiment = 0.7 * score_text(title) + 0.3 * score_text(summary)
            for t in tickers:
                out.append(Signal(
                    source=f"news/{host}", ticker=t, kind=SignalKind.NEWS, sentiment=sentiment,
                    text=text[:600], url=e.get("link", ""), timestamp=ts, weight=2.0 / len(tickers),
                ))
        return out
