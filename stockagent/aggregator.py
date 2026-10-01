"""Turns raw signals from every sub-agent into ranked per-ticker scores."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta

from .models import Signal, SignalKind, TickerScore, utcnow

# How fast each kind of evidence goes stale, and how much one unit of it is worth.
HALF_LIFE_HOURS = {
    SignalKind.SOCIAL: 12, SignalKind.NEWS: 24, SignalKind.MARKET: 24,
    SignalKind.INSIDER: 24 * 7, SignalKind.POLITICIAN: 24 * 14, SignalKind.SMART_MONEY: 24 * 30,
}
KIND_MULTIPLIER = {
    SignalKind.SOCIAL: 1.0, SignalKind.NEWS: 1.5, SignalKind.MARKET: 1.0,
    SignalKind.INSIDER: 3.0, SignalKind.POLITICIAN: 2.0, SignalKind.SMART_MONEY: 3.0,
}
SMART_KINDS = {SignalKind.SMART_MONEY, SignalKind.INSIDER, SignalKind.POLITICIAN}


def lookback_for(kind: SignalKind) -> timedelta:
    return timedelta(hours=HALF_LIFE_HOURS[kind] * 4)


def max_lookback() -> timedelta:
    return max(lookback_for(k) for k in SignalKind)


def decay(sig: Signal, now: datetime) -> float:
    age_h = max(0.0, (now - sig.timestamp).total_seconds() / 3600)
    if age_h > lookback_for(sig.kind).total_seconds() / 3600:
        return 0.0
    return 0.5 ** (age_h / HALF_LIFE_HOURS[sig.kind])


def aggregate(signals: list[Signal], baseline_daily_mentions: dict[str, float] | None = None,
              now: datetime | None = None, evidence_per_ticker: int = 8) -> list[TickerScore]:
    """Score every ticker. Positive score = bullish, negative = bearish; |score| = strength."""
    now = now or utcnow()
    baseline = baseline_daily_mentions or {}
    by_ticker: dict[str, list[tuple[Signal, float]]] = defaultdict(list)
    for s in signals:
        d = decay(s, now)
        if d > 0:
            by_ticker[s.ticker].append((s, d))

    scores = []
    for ticker, items in by_ticker.items():
        ts = TickerScore(ticker=ticker)
        conviction = attention = sent_num = sent_den = 0.0
        recent_mentions = 0
        for s, d in items:
            w = s.weight * d
            conviction += w * s.sentiment * KIND_MULTIPLIER[s.kind]
            sent_num += w * s.sentiment
            sent_den += w
            if s.kind in (SignalKind.SOCIAL, SignalKind.NEWS):
                attention += w
                ts.mentions += 1
                if now - s.timestamp <= timedelta(hours=24):
                    recent_mentions += 1
            if s.kind in SMART_KINDS:
                ts.smart_money += w * s.sentiment
            ts.sources.add(s.source.split("/")[0])
            ts.kinds.add(s.kind)

        ts.sentiment = sent_num / sent_den if sent_den else 0.0
        # Velocity: today's chatter vs. this ticker's normal day (smoothed so new names aren't infinite).
        ts.velocity = (recent_mentions + 1) / (baseline.get(ticker, 0.0) + 1)

        direction = 1 if conviction >= 0 else -1
        score = direction * 2 * math.log1p(abs(conviction))
        score += direction * 0.5 * max(0, len(ts.kinds) - 1)          # corroboration across kinds
        score += direction * 0.4 * max(0, len(ts.sources) - 1)        # ... and across platforms
        if ts.velocity > 1.5 and recent_mentions >= 3:
            score += direction * min(2.0, math.log2(ts.velocity))     # attention spike
        ts.score = round(score, 2)

        ts.evidence = _pick_evidence(items, evidence_per_ticker)
        scores.append(ts)

    scores.sort(key=lambda t: abs(t.score), reverse=True)
    return scores


def _pick_evidence(items: list[tuple[Signal, float]], n: int) -> list[Signal]:
    """Strongest signals first, but round-robin across sources so one subreddit can't fill the list."""
    by_src: dict[str, list[Signal]] = defaultdict(list)
    for s, d in sorted(items, key=lambda x: x[0].weight * x[1] * KIND_MULTIPLIER[x[0].kind], reverse=True):
        by_src[s.source].append(s)
    out: list[Signal] = []
    while len(out) < n and any(by_src.values()):
        for src in list(by_src):
            if by_src[src] and len(out) < n:
                out.append(by_src[src].pop(0))
    return out
