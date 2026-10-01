"""The analyst: Claude reads the top candidates' evidence and writes recommendations.

Falls back to a transparent rule-based analyst when no API key is configured.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .config import AnalystConfig
from .models import TickerScore

log = logging.getLogger(__name__)


class Recommendation(BaseModel):
    ticker: str
    action: Literal["BUY", "SELL", "HOLD", "WATCH", "AVOID"]
    conviction: int = Field(description="1 (weak) to 10 (very strong)")
    time_horizon: Literal["intraday", "swing", "position"]
    thesis: str = Field(description="2-4 sentences grounded in the supplied evidence")
    catalysts: list[str]
    risks: list[str]
    key_sources: list[str] = Field(description="Which source types drove this call, e.g. 'insider buying', 'r/wallstreetbets'")


class AnalystReport(BaseModel):
    market_summary: str = Field(description="What the crowd and smart money are focused on right now, 3-5 sentences")
    recommendations: list[Recommendation]
    red_flags: list[str] = Field(description="Likely pump-and-dumps, coordinated hype, or stale signals to ignore")


SYSTEM_PROMPT = """You are the lead analyst of a stock research desk. Sub-agents monitor Reddit, StockTwits, X, \
Discord trading servers, financial news, superinvestor 13F filings, ARK Invest's daily trades, SEC Form 4 insider \
filings, congressional trade disclosures, and price/volume data. Each run you receive the highest-scoring tickers \
with their aggregated metrics and raw evidence.

How to weigh the evidence:
- Smart money (insider open-market buys, superinvestor new positions, ARK adds) is the most reliable signal, but \
13F data lags up to 45 days and congressional disclosures up to 45 days; say so when a call rests on them.
- Social chatter measures attention, not value. A sudden spike in hype on a small cap with no news, insider, or \
fundamental support is a pump-and-dump warning sign: flag it under red_flags rather than recommending it.
- Agreement across independent source types matters more than volume from one platform.
- A big price move that already happened (see market stats) lowers the reward of chasing it.

Rules:
- Only use the evidence provided. Do not invent prices, earnings figures, or events.
- Prefer WATCH over BUY when evidence is thin, one-sided, or only social. Use SELL/AVOID for bearish setups.
- Keep conviction calibrated: 8+ requires corroboration from at least two independent kinds of evidence.
- Return between 3 and {max_recs} recommendations, ordered by conviction."""


def build_candidate_payload(candidates: list[TickerScore]) -> list[dict]:
    return [{
        "ticker": c.ticker,
        "score": c.score,
        "net_sentiment": round(c.sentiment, 2),
        "mentions": c.mentions,
        "attention_velocity_vs_baseline": round(c.velocity, 2),
        "smart_money_flow": round(c.smart_money, 2),
        "source_types": sorted(k.value for k in c.kinds),
        "platforms": sorted(c.sources),
        "market": c.market or "unavailable",
        "evidence": [{
            "source": s.source, "kind": s.kind.value, "sentiment": round(s.sentiment, 2),
            "when": s.timestamp.strftime("%Y-%m-%d %H:%M UTC"), "text": s.text[:400], "url": s.url,
        } for s in c.evidence],
    } for c in candidates]


def _task_prompt(candidates: list[TickerScore], mode: str) -> str:
    task = ("This is the DAILY DIGEST: give the full picture for the next trading session."
            if mode == "daily" else
            "This is a REALTIME ALERT: these tickers just crossed the alert threshold. Be brief and decisive.")
    return f"{task}\n\nCandidates (JSON):\n" + json.dumps(build_candidate_payload(candidates), indent=1)


class Analyst:
    def __init__(self, cfg: AnalystConfig):
        self.cfg = cfg
        self.client = None
        self.claude_cli: str | None = None
        self.backend = self._pick_backend()
        if self.backend == "api":
            self.client = anthropic.AsyncAnthropic()
        log.info("Analyst backend: %s", self.backend)

    def _pick_backend(self) -> str:
        cfg = self.cfg
        if not cfg.enabled or cfg.backend == "rules":
            return "rules"
        if cfg.backend in ("auto", "api") and _has_credentials():
            return "api"
        if cfg.backend in ("auto", "claude-code"):
            self.claude_cli = shutil.which("claude")
            if self.claude_cli:
                return "claude-code"
            if cfg.backend == "claude-code":
                log.error("backend is claude-code but the `claude` command is not on PATH")
        elif cfg.backend == "api":
            log.error("backend is api but ANTHROPIC_API_KEY is not set")
        return "rules"

    @property
    def uses_llm(self) -> bool:
        return self.backend != "rules"

    async def analyze(self, candidates: list[TickerScore], mode: str = "daily") -> AnalystReport:
        if not candidates:
            return AnalystReport(market_summary="No qualifying signals in the lookback window.",
                                 recommendations=[], red_flags=[])
        try:
            if self.backend == "api":
                return await self._claude(candidates, mode)
            if self.backend == "claude-code":
                return await self._claude_code(candidates, mode)
        except anthropic.APIStatusError as e:
            log.error("Claude API error %s: %s - falling back to rule-based analyst", e.status_code, e.message)
        except anthropic.APIConnectionError as e:
            log.error("Could not reach Claude API (%s) - falling back to rule-based analyst", e)
        except ClaudeCodeError as e:
            log.error("Claude Code analyst failed (%s) - falling back to rule-based analyst", e)
        return rule_based_report(candidates)

    async def _claude_code(self, candidates: list[TickerScore], mode: str) -> AnalystReport:
        """Run the analysis through the Claude Code CLI, which uses its own login
        (e.g. a Claude Pro/Max subscription) instead of an API key."""
        max_recs = min(10, len(candidates))
        cmd = [
            self.claude_cli, "-p", "--output-format", "json", "--no-session-persistence",
            "--tools", "",  # pure analysis: no file, shell or web access
            "--model", self.cfg.model, "--effort", self.cfg.effort,
            "--system-prompt", SYSTEM_PROMPT.format(max_recs=max_recs),
            "--json-schema", json.dumps(AnalystReport.model_json_schema()),
        ]
        # Drop API credentials so the CLI uses the subscription login, not pay-as-you-go billing.
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env)
        try:
            # The prompt goes over stdin: it can exceed Windows' command-line length limit.
            out, err = await asyncio.wait_for(proc.communicate(_task_prompt(candidates, mode).encode("utf-8")),
                                              timeout=self.cfg.claude_code_timeout_seconds)
        except asyncio.TimeoutError:
            proc.kill()
            raise ClaudeCodeError(f"timed out after {self.cfg.claude_code_timeout_seconds}s")
        return parse_claude_code_output(proc.returncode, out, err)

    async def _claude(self, candidates: list[TickerScore], mode: str) -> AnalystReport:
        max_recs = min(10, len(candidates))
        response = await self.client.beta.messages.parse(
            model=self.cfg.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT.format(max_recs=max_recs),
            thinking={"type": "adaptive"},
            output_config={"effort": self.cfg.effort},
            output_format=AnalystReport,
            # If a safety classifier declines, the API reruns the request on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": _task_prompt(candidates, mode)}],
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            log.warning("Analyst returned no structured output (stop_reason=%s)", response.stop_reason)
            return rule_based_report(candidates)
        return response.parsed_output


class ClaudeCodeError(RuntimeError):
    pass


def parse_claude_code_output(returncode: int | None, out: bytes, err: bytes) -> AnalystReport:
    text = out.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        msg = (err.decode("utf-8", errors="replace") or text).strip()[:300]
        raise ClaudeCodeError(f"exit code {returncode}: {msg or 'no output'}")
    if data.get("is_error") or data.get("subtype") != "success":
        raise ClaudeCodeError(str(data.get("result") or data.get("subtype"))[:300])
    structured = data.get("structured_output")
    if structured is None:
        try:
            structured = json.loads(data.get("result") or "")
        except json.JSONDecodeError:
            raise ClaudeCodeError("response had no structured output")
    try:
        return AnalystReport.model_validate(structured)
    except ValueError as e:
        raise ClaudeCodeError(f"structured output did not match schema: {e}")


def _has_credentials() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_PROFILE"))


def rule_based_report(candidates: list[TickerScore]) -> AnalystReport:
    """Deterministic fallback: maps score + corroboration to an action."""
    recs, flags = [], []
    for c in candidates[:10]:
        smart = c.smart_money
        social_only = all(k.value in ("social", "market") for k in c.kinds)
        if social_only and c.velocity >= 5 and c.score > 0:
            flags.append(f"{c.ticker}: {c.velocity:.0f}x normal chatter with no news or smart-money support")
            continue
        if c.score >= 6 and not social_only:
            action = "BUY"
        elif c.score >= 3:
            action = "WATCH"
        elif c.score <= -6:
            action = "SELL"
        elif c.score <= -3:
            action = "AVOID"
        else:
            action = "HOLD"
        conviction = max(1, min(10, round(abs(c.score)) - (2 if social_only else 0)))
        recs.append(Recommendation(
            ticker=c.ticker, action=action, conviction=conviction,
            time_horizon="position" if abs(smart) > 1 else "swing",
            thesis=(f"Score {c.score:+.1f} from {c.mentions} mentions across {', '.join(sorted(c.sources))}; "
                    f"net sentiment {c.sentiment:+.2f}, attention {c.velocity:.1f}x baseline, "
                    f"smart-money flow {smart:+.1f}."),
            catalysts=[e.text[:140] for e in c.evidence if e.kind.value in ("smart_money", "insider", "news")][:3],
            risks=["Rule-based fallback: no LLM review of the evidence"] + (["Social-only signal"] if social_only else []),
            key_sources=sorted(k.value for k in c.kinds),
        ))
    recs.sort(key=lambda r: r.conviction, reverse=True)
    return AnalystReport(
        market_summary=("Rule-based summary (set ANTHROPIC_API_KEY or install Claude Code for Claude analysis). "
                        "Most active: "
                        + ", ".join(f"{c.ticker} ({c.score:+.1f})" for c in candidates[:5])),
        recommendations=recs, red_flags=flags,
    )
