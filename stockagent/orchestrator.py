"""Orchestrator: runs every sub-agent on its own cadence, scores, analyzes, notifies."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .aggregator import SMART_KINDS, aggregate, max_lookback
from .analyst import Analyst, AnalystReport
from .config import Settings
from .models import SignalKind, TickerScore, utcnow
from .notifier import Notifier
from .sources import REGISTRY, SourceAgent
from .sources.market import fetch_stats
from .store import Store
from .tickers import TickerUniverse

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.data_path / "stockagent.db")
        self.analyst = Analyst(settings.analyst)
        self.notifier = Notifier(settings.notify)
        self.universe = TickerUniverse()
        self.agents: dict[str, SourceAgent] = {}
        self.status: dict[str, str] = {}

    async def setup(self) -> None:
        self.universe = await TickerUniverse.load(self.settings.data_path, self.settings.sec_user_agent)
        log.info("Ticker universe: %d symbols", len(self.universe.symbols))
        for name, cfg in self.settings.sources.items():
            cls = REGISTRY.get(name)
            if cls is None:
                log.warning("Unknown source %r in config, skipping", name)
            elif cfg.enabled:
                self.agents[name] = cls(cfg.options, self.universe, self.store,
                                        user_agent=self.settings.sec_user_agent,
                                        watchlist=self.settings.watchlist)
            else:
                self.status[name] = "disabled"

    # ---- collection ----------------------------------------------------------------

    async def run_agent(self, agent: SourceAgent) -> int:
        signals = await agent.run_once()
        new = self.store.add_signals(signals)
        self.status[agent.name] = (f"error: {agent.last_error}" if agent.last_error and not signals
                                   else f"ok, {len(new)} new / {len(signals)} signals @ {utcnow():%H:%M} UTC")
        log.info("[%s] %s", agent.name, self.status[agent.name])
        return len(new)

    async def sweep(self) -> int:
        """Run every sub-agent once, concurrently."""
        results = await asyncio.gather(*(self.run_agent(a) for a in self.agents.values()))
        return sum(results)

    # ---- scoring / analysis --------------------------------------------------------

    def score(self) -> list[TickerScore]:
        now = utcnow()
        signals = self.store.signals_since(now - max_lookback())
        week = self.store.mention_counts(now - timedelta(days=8), now - timedelta(days=1))
        baseline = {t: n / 7 for t, n in week.items()}
        return aggregate(signals, baseline, now)

    def candidates(self, scores: list[TickerScore]) -> list[TickerScore]:
        def meaningful(c: TickerScore) -> bool:
            return c.mentions >= 3 or bool(c.kinds & SMART_KINDS) or (SignalKind.MARKET in c.kinds and c.mentions >= 1)
        return [c for c in scores if meaningful(c)][: self.settings.analyst.max_candidates]

    async def analyze(self, mode: str = "daily", candidates: list[TickerScore] | None = None) -> AnalystReport:
        cands = candidates if candidates is not None else self.candidates(self.score())
        stats = await fetch_stats([c.ticker for c in cands])
        for c in cands:
            c.market = stats.get(c.ticker, {})
        report = await self.analyst.analyze(cands, mode)
        now = datetime.now(ZoneInfo(self.settings.schedule.timezone))
        path = await self.notifier.send(report, cands, mode, now, self.status)
        self.store.save_recommendations(mode, report.model_dump())
        if path:
            log.info("Report written to %s", path)
        return report

    async def once(self) -> AnalystReport:
        await self.setup()
        await self.sweep()
        return await self.analyze("daily")

    # ---- realtime daemon -----------------------------------------------------------

    async def _agent_loop(self, agent: SourceAgent) -> None:
        interval = self.settings.sources[agent.name].interval_seconds
        while True:
            await self.run_agent(agent)
            await asyncio.sleep(interval)

    async def _alert_loop(self) -> None:
        sch = self.settings.schedule
        while True:
            await asyncio.sleep(sch.rescore_seconds)
            try:
                hot = self.pick_alerts(self.candidates(self.score()))
                if hot:
                    log.info("Realtime alert for %s", [c.ticker for c in hot])
                    await self.analyze("realtime", hot)
            except Exception:
                log.exception("Alert loop iteration failed")

    def pick_alerts(self, cands: list[TickerScore]) -> list[TickerScore]:
        sch = self.settings.schedule
        last: dict = self.store.get_state("alerts:last", {})
        now = utcnow()
        hot = []
        for c in cands:
            if abs(c.score) < sch.alert_score_threshold:
                continue
            prev = last.get(c.ticker)
            if prev:
                age = now - datetime.fromisoformat(prev["at"])
                if age < timedelta(hours=sch.alert_cooldown_hours) and abs(c.score) < abs(prev["score"]) + 2:
                    continue
            hot.append(c)
            last[c.ticker] = {"at": now.isoformat(), "score": c.score}
        self.store.set_state("alerts:last", last)
        return hot[:5]

    def next_digest(self, now: datetime | None = None) -> datetime:
        sch = self.settings.schedule
        tz = ZoneInfo(sch.timezone)
        now = (now or utcnow()).astimezone(tz)
        hh, mm = map(int, sch.daily_digest_time.split(":"))
        nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        while nxt <= now or (sch.weekdays_only and nxt.weekday() >= 5):
            nxt += timedelta(days=1)
        return nxt

    async def _digest_loop(self) -> None:
        while True:
            nxt = self.next_digest()
            log.info("Next daily digest at %s", nxt.isoformat())
            await asyncio.sleep(max(1.0, (nxt - utcnow()).total_seconds()))
            try:
                await self.analyze("daily")
                self.store.prune()
            except Exception:
                log.exception("Daily digest failed")

    async def run_forever(self) -> None:
        await self.setup()
        log.info("Starting %d sub-agents: %s", len(self.agents), ", ".join(self.agents))
        tasks = [asyncio.create_task(self._agent_loop(a), name=a.name) for a in self.agents.values()]
        tasks += [asyncio.create_task(self._alert_loop(), name="alerts"),
                  asyncio.create_task(self._digest_loop(), name="digest")]
        await asyncio.gather(*tasks)
