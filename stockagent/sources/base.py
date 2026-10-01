"""Base class for every sub-agent (one per information source)."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import httpx

from ..models import Signal

if TYPE_CHECKING:
    from ..store import Store
    from ..tickers import TickerUniverse

log = logging.getLogger(__name__)

DEFAULT_UA = "Mozilla/5.0 (compatible; myStockAgent/0.1)"


class SourceAgent(ABC):
    """A sub-agent that watches one source and emits Signals.

    Subclasses implement `fetch()`. The orchestrator calls `run_once()` on the
    agent's own interval, so each source polls at the cadence it supports.
    """

    name: str = "base"
    # Env vars that must be set for this source to run (e.g. API tokens).
    requires_env: tuple[str, ...] = ()

    def __init__(self, options: dict[str, Any], universe: "TickerUniverse", store: "Store",
                 user_agent: str = DEFAULT_UA, watchlist: list[str] | None = None):
        self.options = options
        self.universe = universe
        self.store = store
        self.user_agent = user_agent
        self.watchlist = [t.upper() for t in (watchlist or [])]
        self.last_error: str | None = None

    def missing_env(self) -> list[str]:
        import os
        return [e for e in self.requires_env if not os.getenv(e)]

    def client(self, **kw) -> httpx.AsyncClient:
        headers = {"User-Agent": self.user_agent, **kw.pop("headers", {})}
        return httpx.AsyncClient(timeout=kw.pop("timeout", 20), headers=headers,
                                 follow_redirects=True, **kw)

    async def get_json(self, client: httpx.AsyncClient, url: str, retries: int = 2, **kw) -> Any:
        for attempt in range(retries + 1):
            r = await client.get(url, **kw)
            if r.status_code == 429 and attempt < retries:
                await asyncio.sleep(float(r.headers.get("retry-after", 2 ** (attempt + 1))))
                continue
            r.raise_for_status()
            return r.json()

    @abstractmethod
    async def fetch(self) -> list[Signal]:
        ...

    async def run_once(self) -> list[Signal]:
        missing = self.missing_env()
        if missing:
            self.last_error = f"missing env: {', '.join(missing)}"
            return []
        try:
            signals = await self.fetch()
            self.last_error = None
            return signals
        except Exception as e:  # one broken source must never take the agent down
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("[%s] fetch failed: %s", self.name, self.last_error)
            return []
