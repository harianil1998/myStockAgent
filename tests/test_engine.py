import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from stockagent import analyst as analyst_mod
from stockagent import orchestrator as orch_mod
from stockagent.aggregator import aggregate, decay
from stockagent.analyst import Analyst, AnalystReport, Recommendation, rule_based_report
from stockagent.config import AnalystConfig, Settings
from stockagent.models import Signal, SignalKind
from stockagent.sources.base import SourceAgent

NOW = datetime(2026, 10, 1, 15, tzinfo=timezone.utc)


def sig(ticker, kind=SignalKind.SOCIAL, sentiment=0.5, hours_ago=1, source="reddit/r/x", weight=1.0, text=""):
    return Signal(source=source, ticker=ticker, kind=kind, sentiment=sentiment, weight=weight,
                  text=text or f"{ticker} {kind.value} {hours_ago} {source}", timestamp=NOW - timedelta(hours=hours_ago))


def test_decay_by_kind():
    assert decay(sig("A", hours_ago=12), NOW) == pytest.approx(0.5)
    assert decay(sig("A", hours_ago=60), NOW) == 0.0  # beyond 4 half-lives for social
    assert decay(sig("A", SignalKind.SMART_MONEY, hours_ago=24 * 20), NOW) > 0.5


def test_corroborated_smart_money_outranks_hype():
    signals = [sig("HYPE", sentiment=0.6) for _ in range(6)]
    signals += [sig("REAL", sentiment=0.4, source="stocktwits"),
                sig("REAL", SignalKind.INSIDER, 0.9, 24, "sec/form4", 3.0),
                sig("REAL", SignalKind.SMART_MONEY, 0.9, 24 * 10, "13f", 5.0)]
    signals += [sig("BEAR", SignalKind.NEWS, -0.8, source="news/x", weight=2) for _ in range(3)]
    scores = {s.ticker: s for s in aggregate(signals, now=NOW)}
    assert scores["REAL"].score > scores["HYPE"].score > 0
    assert scores["BEAR"].score < 0
    assert scores["REAL"].smart_money > 0 and SignalKind.INSIDER in scores["REAL"].kinds


def test_velocity_uses_baseline():
    signals = [sig("NVDA") for _ in range(10)]
    hot = aggregate(signals, {"NVDA": 0.0}, NOW)[0]
    normal = aggregate(signals, {"NVDA": 20.0}, NOW)[0]
    assert hot.velocity > 5 > normal.velocity and hot.score > normal.score


def test_evidence_is_diversified():
    signals = [sig("X", source="reddit/r/a", weight=10) for _ in range(10)] + [sig("X", source="news/b")]
    ev = aggregate(signals, now=NOW, evidence_per_ticker=3)[0].evidence
    assert {"reddit/r/a", "news/b"} <= {e.source for e in ev}


def test_rule_based_flags_pure_hype():
    signals = [sig("PUMP", sentiment=0.9, source=f"reddit/r/{i}") for i in range(12)]
    rep = rule_based_report(aggregate(signals, {"PUMP": 0.0}, NOW))
    assert rep.recommendations == [] and "PUMP" in rep.red_flags[0]


class FakeMessages:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    async def parse(self, **kw):
        self.kwargs = kw
        return self.response


async def test_claude_analyst_request_shape():
    report = AnalystReport(market_summary="s", red_flags=[], recommendations=[Recommendation(
        ticker="NVDA", action="BUY", conviction=8, time_horizon="swing", thesis="t",
        catalysts=[], risks=[], key_sources=["insider"])])
    a = Analyst(AnalystConfig(backend="rules"))
    msgs = FakeMessages(SimpleNamespace(stop_reason="end_turn", parsed_output=report))
    a.backend, a.client = "api", SimpleNamespace(beta=SimpleNamespace(messages=msgs))
    out = await a.analyze(aggregate([sig("NVDA")], now=NOW))
    assert out.recommendations[0].ticker == "NVDA"
    kw = msgs.kwargs
    assert kw["model"] == "claude-opus-5-5" and kw["output_format"] is AnalystReport
    assert kw["thinking"] == {"type": "adaptive"} and kw["fallbacks"] == "default"
    assert '"ticker": "NVDA"' in kw["messages"][0]["content"]


async def test_claude_refusal_falls_back():
    a = Analyst(AnalystConfig(backend="rules"))
    a.backend, a.client = "api", SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(
        SimpleNamespace(stop_reason="refusal", parsed_output=None))))
    out = await a.analyze(aggregate([sig("NVDA")], now=NOW))
    assert out.market_summary.startswith("Rule-based")


def test_backend_selection(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_PROFILE", raising=False)
    monkeypatch.setattr(analyst_mod.shutil, "which", lambda name: "/usr/bin/claude")
    assert Analyst(AnalystConfig()).backend == "claude-code"
    assert Analyst(AnalystConfig(backend="api")).backend == "rules"  # no key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert Analyst(AnalystConfig()).backend == "api"  # key wins in auto
    assert Analyst(AnalystConfig(backend="claude-code")).backend == "claude-code"
    monkeypatch.setattr(analyst_mod.shutil, "which", lambda name: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert Analyst(AnalystConfig()).backend == "rules"


class FakeProc:
    def __init__(self, out: bytes, returncode: int = 0):
        self.out, self.returncode, self.stdin_data = out, returncode, None

    async def communicate(self, data):
        self.stdin_data = data
        return self.out, b""

    def kill(self):
        pass


async def test_claude_code_backend(monkeypatch):
    report = {"market_summary": "s", "red_flags": [], "recommendations": [{
        "ticker": "NVDA", "action": "WATCH", "conviction": 5, "time_horizon": "swing", "thesis": "t",
        "catalysts": [], "risks": [], "key_sources": ["social"]}]}
    proc = FakeProc(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                "result": "", "structured_output": report}).encode())
    calls = {}

    async def fake_exec(*cmd, **kw):
        calls["cmd"], calls["env"] = cmd, kw["env"]
        return proc
    monkeypatch.setattr(analyst_mod.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    a = Analyst(AnalystConfig(backend="rules"))
    a.backend, a.claude_cli = "claude-code", "claude"
    out = await a.analyze(aggregate([sig("NVDA")], now=NOW))
    assert out.recommendations[0].action == "WATCH"
    cmd = calls["cmd"]
    assert cmd[:2] == ("claude", "-p") and cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert "ANTHROPIC_API_KEY" not in calls["env"]  # uses the subscription login, not the key
    assert b'"ticker": "NVDA"' in proc.stdin_data


def test_claude_code_output_errors():
    with pytest.raises(analyst_mod.ClaudeCodeError, match="not logged in"):
        analyst_mod.parse_claude_code_output(1, b"", b"not logged in")
    err = json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "x"}).encode()
    with pytest.raises(analyst_mod.ClaudeCodeError):
        analyst_mod.parse_claude_code_output(0, err, b"")
    bad = json.dumps({"subtype": "success", "is_error": False, "structured_output": {"nope": 1}}).encode()
    with pytest.raises(analyst_mod.ClaudeCodeError, match="schema"):
        analyst_mod.parse_claude_code_output(0, bad, b"")


class FakeAgent(SourceAgent):
    name = "fake"

    async def fetch(self):
        now = datetime.now(timezone.utc)
        return [Signal(source="fake", ticker="NVDA", kind=SignalKind.SOCIAL, sentiment=0.7, text=f"hi {i}",
                       timestamp=now) for i in range(4)] + [
                Signal(source="sec/form4", ticker="NVDA", kind=SignalKind.INSIDER, sentiment=0.9,
                       text="CEO bought", weight=3, timestamp=now)]


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(data_dir=str(tmp_path / "data"), analyst={"backend": "rules"},
                        notify={"console": False, "report_dir": str(tmp_path / "reports")},
                        sources={n: {"enabled": False} for n in
                                 ["reddit", "stocktwits", "twitter", "discord", "news", "superinvestors",
                                  "ark", "insiders", "congress", "market"]})

    async def no_stats(tickers):
        return {"NVDA": {"price": 1.0, "chg_1d": 2.0}}

    async def no_universe(*a, **k):
        from stockagent.tickers import TickerUniverse
        return TickerUniverse()
    monkeypatch.setattr(orch_mod, "fetch_stats", no_stats)
    monkeypatch.setattr(orch_mod.TickerUniverse, "load", no_universe)
    o = orch_mod.Orchestrator(settings)
    return o


async def test_end_to_end_sweep_and_report(orch, tmp_path):
    await orch.setup()
    assert orch.agents == {} and orch.status["reddit"] == "disabled"
    orch.agents["fake"] = FakeAgent({}, orch.universe, orch.store)
    assert await orch.sweep() == 5
    assert await orch.sweep() == 0  # dedup on re-poll
    report = await orch.analyze("daily")
    assert report.recommendations[0].ticker == "NVDA"
    md = next((tmp_path / "reports").glob("*_daily.md")).read_text(encoding="utf-8")
    assert "NVDA" in md and "Not financial advice" in md


async def test_alert_cooldown(orch):
    await orch.setup()
    orch.settings.schedule.alert_score_threshold = 1.0
    cands = aggregate([sig("NVDA", weight=5, hours_ago=0) for _ in range(5)], now=datetime.now(timezone.utc))
    assert [c.ticker for c in orch.pick_alerts(cands)] == ["NVDA"]
    assert orch.pick_alerts(cands) == []  # same score inside cooldown -> no repeat alert


def test_next_digest_skips_weekend(orch):
    orch.settings.schedule.daily_digest_time = "08:30"
    # Friday 2026-10-02 15:00 UTC (11:00 New York) -> next is Monday 08:30 New York.
    nxt = orch.next_digest(datetime(2026, 10, 2, 15, tzinfo=timezone.utc))
    assert (nxt.weekday(), nxt.hour, nxt.minute) == (0, 8, 30)


def test_settings_merge_default_options():
    s = Settings(sources={"reddit": {"options": {"limit": 10}}})
    assert s.sources["reddit"].options["limit"] == 10
    assert s.sources["reddit"].interval_seconds == 180  # default kept when not overridden
    assert "wallstreetbets" in s.sources["reddit"].options["subreddits"]
    assert s.sources["insiders"].enabled
