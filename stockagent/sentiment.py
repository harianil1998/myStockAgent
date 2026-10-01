"""Fast lexicon sentiment for finance chatter.

Deliberately cheap: it runs on every social post. The Claude analyst does the
nuanced reading later, only on the top candidates.
"""

from __future__ import annotations

import re

BULLISH = {
    "buy": 1.0, "buying": 1.0, "bought": 0.8, "long": 0.8, "calls": 1.0, "call": 0.5,
    "moon": 1.2, "mooning": 1.2, "rocket": 1.0, "squeeze": 0.8, "breakout": 1.0,
    "bullish": 1.5, "undervalued": 1.0, "upgrade": 1.2, "upgraded": 1.2, "beat": 1.0,
    "beats": 1.0, "surge": 1.0, "surges": 1.0, "soar": 1.0, "soars": 1.0, "rally": 0.8,
    "record": 0.5, "strong": 0.6, "growth": 0.5, "outperform": 1.0, "accumulate": 0.8,
    "tendies": 1.0, "rip": 0.6, "ripping": 0.8, "raised": 0.6, "raises": 0.6, "buyback": 0.8,
    "approval": 0.8, "approved": 0.8, "partnership": 0.6, "acquire": 0.4, "dividend": 0.4,
    "🚀": 1.2, "📈": 1.0, "💎": 0.6, "🐂": 1.0,
}
BEARISH = {
    "sell": 1.0, "selling": 1.0, "sold": 0.6, "short": 0.9, "shorting": 1.0, "puts": 1.0,
    "put": 0.5, "dump": 1.2, "dumping": 1.2, "crash": 1.2, "bearish": 1.5, "overvalued": 1.0,
    "downgrade": 1.2, "downgraded": 1.2, "miss": 1.0, "misses": 1.0, "missed": 1.0,
    "plunge": 1.2, "plunges": 1.2, "tank": 1.0, "tanking": 1.0, "bagholder": 0.8,
    "bankrupt": 1.5, "bankruptcy": 1.5, "fraud": 1.5, "lawsuit": 0.8, "probe": 0.8,
    "recall": 0.8, "layoffs": 0.6, "cut": 0.5, "cuts": 0.5, "weak": 0.6, "warning": 0.7,
    "dilution": 1.0, "offering": 0.6, "delisted": 1.5, "rugpull": 1.5, "underperform": 1.0,
    "📉": 1.0, "🐻": 1.0, "🌈🐻": 1.2,
}
NEGATORS = {"not", "no", "never", "don't", "dont", "isn't", "isnt", "won't", "wont", "without"}

_TOKEN = re.compile(r"[a-z']+|[\U0001F300-\U0001FAFF]")


def score_text(text: str) -> float:
    """Return sentiment in [-1, 1]."""
    tokens = _TOKEN.findall(text.lower())
    total = 0.0
    hits = 0
    for i, tok in enumerate(tokens):
        val = BULLISH.get(tok, 0.0) - BEARISH.get(tok, 0.0)
        if not val:
            continue
        if any(t in NEGATORS for t in tokens[max(0, i - 2):i]):
            val = -val
        total += val
        hits += 1
    if not hits:
        return 0.0
    # Squash: a couple of strong words saturate around +/-0.8.
    return max(-1.0, min(1.0, total / (hits + 1.0)))
