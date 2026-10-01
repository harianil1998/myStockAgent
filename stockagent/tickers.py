"""Ticker extraction with a symbol universe to keep false positives down."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Uppercase words that are real tickers but almost always mean something else in chatter.
STOPWORDS = {
    "A", "I", "AM", "AN", "ARE", "AT", "BE", "BY", "CAN", "DD", "DO", "FOR", "GO", "HAS", "HE",
    "IF", "IN", "IS", "IT", "ME", "MY", "NO", "NOW", "OF", "OK", "ON", "OR", "SO", "TO", "UP",
    "US", "WE", "ALL", "AND", "ANY", "ARE", "BIG", "CEO", "CFO", "EPS", "ETF", "FDA", "FED",
    "GDP", "IPO", "IMO", "LOL", "NEW", "ONE", "OUT", "PM", "RH", "SEC", "THE", "USA", "YOLO",
    "ATH", "EOD", "EOW", "OTM", "ITM", "IV", "TA", "PE", "AI", "EV", "CPI", "FOMC", "LMAO",
    "HOLD", "BUY", "SELL", "CALL", "PUT", "PUTS", "MOON", "WSB", "OP", "EDIT", "TLDR", "USD",
    "NYSE", "GAIN", "LOSS", "RIP", "FOMO", "HODL", "BEAT", "REAL", "BEST", "GOOD", "LOVE",
    "VERY", "WELL", "JUST", "LIKE", "EVER", "SEE", "SAY", "TOP", "FUND", "PLAY", "TECH",
    "OPEN", "LOW", "HIGH", "NEXT", "OLD", "CASH", "FREE", "FAST", "LIFE", "MAN", "CAR",
}

CASHTAG = re.compile(r"\$([A-Za-z]{1,5}(?:\.[A-Za-z])?)\b")
BARE = re.compile(r"\b([A-Z]{2,5})\b")


class TickerUniverse:
    """Known listed symbols (from SEC) plus issuer-name lookup used for 13F mapping."""

    def __init__(self, symbols: set[str] | None = None, names: dict[str, str] | None = None):
        self.symbols = {s.upper() for s in (symbols or set())}
        self.names = names or {}  # normalized issuer name -> ticker

    @classmethod
    async def load(cls, data_dir: Path, user_agent: str, max_age_hours: int = 24) -> "TickerUniverse":
        cache = data_dir / "company_tickers.json"
        raw = None
        if cache.exists() and time.time() - cache.stat().st_mtime < max_age_hours * 3600:
            raw = json.loads(cache.read_text(encoding="utf-8"))
        else:
            try:
                async with httpx.AsyncClient(timeout=20, headers={"User-Agent": user_agent}) as c:
                    r = await c.get(SEC_TICKERS_URL)
                    r.raise_for_status()
                    raw = r.json()
                    cache.write_text(json.dumps(raw), encoding="utf-8")
            except Exception as e:  # network down, SEC throttling, ...
                log.warning("Could not refresh SEC ticker list (%s); using cache if any", e)
                if cache.exists():
                    raw = json.loads(cache.read_text(encoding="utf-8"))
        return cls.from_sec_json(raw or {})

    @classmethod
    def from_sec_json(cls, raw: dict) -> "TickerUniverse":
        symbols, names = set(), {}
        for row in raw.values():
            t = str(row.get("ticker", "")).upper()
            if not t:
                continue
            symbols.add(t)
            names.setdefault(normalize_issuer(row.get("title", "")), t)
        return cls(symbols, names)

    def __contains__(self, t: str) -> bool:
        return t.upper() in self.symbols

    def __bool__(self) -> bool:
        return bool(self.symbols)

    def lookup_issuer(self, name: str) -> str | None:
        return self.names.get(normalize_issuer(name))

    def extract(self, text: str) -> list[str]:
        """Cashtags always count (if known, or if we have no universe);
        bare uppercase words count only if they're known symbols and not stopwords."""
        found: dict[str, None] = {}
        for m in CASHTAG.finditer(text):
            t = m.group(1).upper()
            if not self.symbols or t in self.symbols:
                found[t] = None
        if self.symbols:
            for m in BARE.finditer(text):
                t = m.group(1)
                if t in self.symbols and t not in STOPWORDS:
                    found[t] = None
        return list(found)


_SUFFIXES = re.compile(
    r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|HOLDINGS?|GROUP|LP|LLC|SA|NV|AG|"
    r"CL [A-Z]|CLASS [A-Z]|COM|NEW|DEL|THE|ADR|SPONSORED|ORD|SHS)\b"
)


def normalize_issuer(name: str) -> str:
    n = re.sub(r"[^A-Z0-9 ]", " ", name.upper())
    n = _SUFFIXES.sub(" ", n)
    return " ".join(n.split())
