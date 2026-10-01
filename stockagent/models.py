"""Core data types shared by every sub-agent."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class SignalKind(str, Enum):
    SOCIAL = "social"          # Reddit, StockTwits, X, Discord chatter
    NEWS = "news"              # Headlines / articles
    SMART_MONEY = "smart_money"  # 13F superinvestors, ARK trades
    INSIDER = "insider"        # SEC Form 4 open-market buys/sells
    POLITICIAN = "politician"  # Congressional trades
    MARKET = "market"          # Price / volume anomalies


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Signal:
    """One observation about one ticker from one source."""

    source: str
    ticker: str
    kind: SignalKind
    sentiment: float  # -1.0 (bearish) .. +1.0 (bullish)
    text: str
    url: str = ""
    author: str = ""
    timestamp: datetime = field(default_factory=utcnow)
    # Relative importance within the source (e.g. upvotes, $ size of a trade), >= 0.
    weight: float = 1.0
    extra: dict = field(default_factory=dict)
    id: str = ""

    def __post_init__(self) -> None:
        self.ticker = self.ticker.upper().lstrip("$")
        self.sentiment = max(-1.0, min(1.0, float(self.sentiment)))
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)
        if not self.id:
            raw = f"{self.source}|{self.ticker}|{self.url}|{self.text[:200]}|{self.author}"
            self.id = hashlib.sha1(raw.encode()).hexdigest()


@dataclass
class TickerScore:
    """Aggregated view of a ticker across all sources."""

    ticker: str
    score: float = 0.0
    mentions: int = 0
    sentiment: float = 0.0
    sources: set[str] = field(default_factory=set)
    kinds: set[SignalKind] = field(default_factory=set)
    smart_money: float = 0.0
    velocity: float = 1.0  # mentions now vs. baseline
    evidence: list[Signal] = field(default_factory=list)
    market: dict = field(default_factory=dict)


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    WATCH = "WATCH"
    AVOID = "AVOID"
