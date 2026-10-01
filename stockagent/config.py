"""Configuration: YAML file + environment variables (secrets live only in env)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class SourceConfig(BaseModel):
    enabled: bool = True
    interval_seconds: int = 300
    options: dict[str, Any] = Field(default_factory=dict)


class AnalystConfig(BaseModel):
    model: str = "claude-opus-5-5"
    effort: str = "high"
    max_candidates: int = 15
    enabled: bool = True


class ScheduleConfig(BaseModel):
    # Local-time HH:MM for the daily digest.
    daily_digest_time: str = "08:30"
    timezone: str = "America/New_York"
    # How often realtime mode re-scores and checks for alerts.
    rescore_seconds: int = 300
    # Score jump that triggers a realtime alert.
    alert_score_threshold: float = 6.0
    weekdays_only: bool = True
    # Don't re-alert the same ticker within this window unless its score jumps.
    alert_cooldown_hours: int = 6


class NotifyConfig(BaseModel):
    console: bool = True
    report_dir: str = "reports"
    discord_webhook_env: str = "DISCORD_WEBHOOK_URL"
    slack_webhook_env: str = "SLACK_WEBHOOK_URL"


DEFAULT_SOURCES: dict[str, dict[str, Any]] = {
    "reddit": {"interval_seconds": 180, "options": {
        "subreddits": ["wallstreetbets", "stocks", "investing", "options", "StockMarket", "pennystocks"],
        "limit": 100}},
    "stocktwits": {"interval_seconds": 120, "options": {"watchlist": []}},
    "twitter": {"interval_seconds": 300, "options": {
        "accounts": [], "query_extra": "-is:retweet lang:en", "max_results": 100}},
    "discord": {"interval_seconds": 120, "options": {"channel_ids": []}},
    "news": {"interval_seconds": 300, "options": {"feeds": [
        "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://seekingalpha.com/market_currents.xml",
        "https://finance.yahoo.com/news/rssindex",
    ]}},
    "superinvestors": {"interval_seconds": 6 * 3600, "options": {}},
    "ark": {"interval_seconds": 3600, "options": {"funds": ["ARKK", "ARKW", "ARKG", "ARKQ", "ARKF", "ARKX"]}},
    "insiders": {"interval_seconds": 900, "options": {"max_filings": 40, "min_value_usd": 100_000}},
    "congress": {"interval_seconds": 3600, "options": {}},
    "market": {"interval_seconds": 600, "options": {}},
}


class Settings(BaseModel):
    data_dir: str = "data"
    sec_user_agent: str = Field(default_factory=lambda: os.getenv("SEC_USER_AGENT", "myStockAgent research@example.com"))
    watchlist: list[str] = Field(default_factory=list)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    analyst: AnalystConfig = Field(default_factory=AnalystConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)

    def model_post_init(self, __context: Any) -> None:
        for name, default in DEFAULT_SOURCES.items():
            if name not in self.sources:
                self.sources[name] = SourceConfig(**default)
            else:
                src = self.sources[name]
                if "interval_seconds" not in src.model_fields_set and "interval_seconds" in default:
                    src.interval_seconds = default["interval_seconds"]
                src.options = {**default.get("options", {}), **src.options}

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    _load_dotenv()
    candidates = [path] if path else ["config.yaml", "config.yml"]
    for c in candidates:
        if c and Path(c).exists():
            raw = yaml.safe_load(Path(c).read_text(encoding="utf-8")) or {}
            return Settings(**raw)
    return Settings()


def _load_dotenv(path: str = ".env") -> None:
    """Tiny .env loader so we don't need python-dotenv. Never overrides real env vars."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)
