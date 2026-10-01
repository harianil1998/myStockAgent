"""Delivers reports: console, markdown files, Discord/Slack webhooks."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

import httpx

from .analyst import AnalystReport
from .config import NotifyConfig
from .models import TickerScore

log = logging.getLogger(__name__)

DISCLAIMER = "_Automated research from public sources. Not financial advice. Do your own due diligence._"
EMOJI = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪", "WATCH": "👀", "AVOID": "⛔"}


def render_markdown(report: AnalystReport, candidates: list[TickerScore], mode: str, at: datetime,
                    source_status: dict[str, str] | None = None) -> str:
    title = "Daily Stock Digest" if mode == "daily" else "Realtime Alert"
    lines = [f"# {title} — {at:%Y-%m-%d %H:%M %Z}", "", report.market_summary, ""]
    if report.recommendations:
        lines += ["## Recommendations", "", "| | Ticker | Action | Conviction | Horizon |", "|---|---|---|---|---|"]
        for r in report.recommendations:
            lines.append(f"| {EMOJI.get(r.action, '')} | **{r.ticker}** | {r.action} | {r.conviction}/10 | {r.time_horizon} |")
        lines.append("")
        for r in report.recommendations:
            lines += [f"### {r.ticker} — {r.action} ({r.conviction}/10)", "", r.thesis, ""]
            if r.catalysts:
                lines += ["**Catalysts:** " + "; ".join(r.catalysts), ""]
            if r.risks:
                lines += ["**Risks:** " + "; ".join(r.risks), ""]
            lines += ["**Driven by:** " + ", ".join(r.key_sources), ""]
    if report.red_flags:
        lines += ["## Red flags", ""] + [f"- {f}" for f in report.red_flags] + [""]
    lines += ["## Signal leaderboard", "", "| Ticker | Score | Sentiment | Mentions | Velocity | Smart $ | Sources |",
              "|---|---|---|---|---|---|---|"]
    for c in candidates:
        lines.append(f"| {c.ticker} | {c.score:+.1f} | {c.sentiment:+.2f} | {c.mentions} | {c.velocity:.1f}x | "
                     f"{c.smart_money:+.1f} | {', '.join(sorted(c.sources))} |")
    if source_status:
        lines += ["", "## Sub-agent status", ""] + [f"- **{k}**: {v}" for k, v in sorted(source_status.items())]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def render_short(report: AnalystReport, mode: str) -> str:
    head = "📊 **Daily Stock Digest**" if mode == "daily" else "🚨 **Realtime Alert**"
    lines = [head, report.market_summary[:600], ""]
    for r in report.recommendations[:8]:
        lines.append(f"{EMOJI.get(r.action, '')} **{r.ticker}** {r.action} ({r.conviction}/10, {r.time_horizon}) — {r.thesis[:220]}")
    if report.red_flags:
        lines.append("⚠️ " + " | ".join(report.red_flags[:3]))
    lines.append(DISCLAIMER)
    return "\n".join(lines)


class Notifier:
    def __init__(self, cfg: NotifyConfig):
        self.cfg = cfg

    async def send(self, report: AnalystReport, candidates: list[TickerScore], mode: str, at: datetime,
                   source_status: dict[str, str] | None = None) -> Path | None:
        md = render_markdown(report, candidates, mode, at, source_status)
        path = None
        if self.cfg.report_dir:
            d = Path(self.cfg.report_dir)
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{at:%Y-%m-%d_%H%M}_{mode}.md"
            path.write_text(md, encoding="utf-8")
        if self.cfg.console:
            print(md)
        short = render_short(report, mode)
        async with httpx.AsyncClient(timeout=15) as c:
            if url := os.getenv(self.cfg.discord_webhook_env):
                await self._post(c, url, {"content": short[:1990]})
            if url := os.getenv(self.cfg.slack_webhook_env):
                await self._post(c, url, {"text": short.replace("**", "*")})
        return path

    @staticmethod
    async def _post(c: httpx.AsyncClient, url: str, payload: dict) -> None:
        try:
            r = await c.post(url, json=payload)
            r.raise_for_status()
        except Exception as e:
            log.warning("Webhook delivery failed: %s", e)
