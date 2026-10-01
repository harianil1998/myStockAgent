# myStockAgent

A multi-source stock research agent. Separate **sub-agents** watch social media, Discord servers,
news, top investors' portfolios, insider and congressional trades, and market data. An
**orchestrator** combines what they find into one score per ticker, and a **Claude analyst**
turns the top candidates into recommendations. You get **realtime alerts** while it runs and a
**daily digest** every morning.

> ⚠️ This is automated research from public data, **not financial advice**. Social signals are
> easy to manipulate, and 13F and congressional data can be up to 45 days old. Do your own due
> diligence before you trade.

## How it works

```
 ┌──────────── sub-agents (each polls on its own interval) ────────────┐
 │ Social        reddit · stocktwits · twitter(X) · discord            │
 │ News          RSS: MarketWatch, CNBC, Seeking Alpha, Yahoo per-ticker│
 │ Smart money   superinvestors (13F) · ark (daily trades)             │
 │ Insiders      SEC Form 4 open-market buys/sells (near-realtime)     │
 │ Politicians   congress (STOCK Act disclosures via Quiver)           │
 │ Market        price / volume anomalies (Yahoo chart API)            │
 └──────────────────────────────┬──────────────────────────────────────┘
                                ▼  Signals(ticker, sentiment, weight, kind, evidence)
                        SQLite store (dedup, history)
                                ▼
          Aggregator: time-decay per kind · source-kind multipliers ·
          cross-source corroboration · attention velocity vs 7-day baseline
                                ▼  ranked candidates + market stats
          Claude analyst (claude-opus-5-5, structured output) — weighs smart
          money over hype, flags pump-and-dumps, calibrates conviction
                                ▼
          Console · Markdown report · Discord / Slack webhook
```

| Sub-agent | What it catches | Cadence | Needs |
|---|---|---|---|
| `reddit` | Posts in r/wallstreetbets, r/stocks, r/investing, r/options, … | 3 min | – |
| `stocktwits` | Trending stream + your watchlist, with users' own Bullish/Bearish labels | 2 min | – |
| `twitter` | Cashtags from accounts you follow + watchlist searches | 5 min | `X_BEARER_TOKEN` |
| `discord` | Alert and trade-idea channels in servers your bot has joined (embeds too) | 2 min | `DISCORD_BOT_TOKEN` |
| `news` | Financial RSS headlines + per-ticker Yahoo Finance news | 5 min | – |
| `superinvestors` | New positions, adds, trims and exits by Buffett, Ackman, Burry, Tepper, Druckenmiller, Klarman, Loeb, Einhorn, Li Lu, Tiger (13F filings) | 6 h | – |
| `ark` | ARK Invest's daily buys and sells (holdings compared day to day) | 1 h | – |
| `insiders` | SEC Form 4 open-market **purchases** by officers and directors, and sales not made under a 10b5-1 plan | 15 min | – |
| `congress` | Trades disclosed by members of Congress | 1 h | `QUIVER_API_KEY` |
| `market` | Unusual volume (≥2× average) or big moves (≥5%) on watchlist and trending names | 10 min | – |

Each sub-agent runs on its own. If one fails (expired token, rate limit, site change), it reports
an error status and the others keep running.

### Scoring

Each signal counts for `weight × time-decay × sentiment × kind-multiplier`:

| Kind | Half-life | Multiplier |
|---|---|---|
| social | 12 h | 1.0 |
| news | 24 h | 1.5 |
| market | 24 h | 1.0 |
| insider | 7 d | 3.0 |
| politician | 14 d | 2.0 |
| smart money (13F/ARK) | 30 d | 3.0 |

A ticker's score gets a bonus when several **independent kinds** of evidence agree (for example
insider buying, a news catalyst and social buzz) and when chatter **spikes** above that ticker's
own 7-day baseline. A positive score is bullish and a negative one is bearish.

## Quick start

```bash
pip install -e ".[dev]"
cp .env.example .env                 # add ANTHROPIC_API_KEY and SEC_USER_AGENT at minimum
cp config.example.yaml config.yaml   # set your watchlist, Discord channels, X accounts

stockagent sources   # run every sub-agent once and show its status
stockagent once      # sweep all sources and write a full report (use this from cron)
stockagent score     # raw signal leaderboard, no LLM
stockagent digest    # report from signals already collected
stockagent run       # realtime daemon: continuous polling, alerts, daily digest
```

Reports are saved to `reports/YYYY-MM-DD_HHMM_{daily|realtime}.md`. Set `DISCORD_WEBHOOK_URL` or
`SLACK_WEBHOOK_URL` to have them posted to a channel as well.

Without `ANTHROPIC_API_KEY` the agent still runs, using a transparent rule-based analyst instead
of Claude.

### Running it every day

- **Always-on (recommended for realtime):** run `stockagent run` on a small VPS, under systemd or
  Docker. Alerts fire when a ticker's |score| crosses `alert_score_threshold`. The digest runs at
  `daily_digest_time` (weekdays by default).
- **Daily only:** `.github/workflows/daily-digest.yml` runs `stockagent once` every weekday at
  08:15 New York time on GitHub Actions and posts to your webhook. Add your keys as repository
  secrets.

## Setting up the optional sources

- **X / Twitter:** create a project at developer.x.com and copy its Bearer Token. Recent search
  needs the Basic tier or higher. List the accounts you trust under `sources.twitter.options.accounts`.
- **Discord:** create a bot at discord.com/developers, enable the **Message Content** intent, and
  invite the bot to servers you belong to. Then turn on Developer Mode, right-click each channel,
  choose *Copy Channel ID*, and add it to `channel_ids`. Respect each server's rules about bots.
- **Congress:** get an API key from quiverquant.com. To use another provider, set
  `sources.congress.options.url` to any JSON feed with the same fields.
- **SEC (13F, Form 4, ticker list):** no key needed, but SEC requires a descriptive
  `SEC_USER_AGENT` such as `"Your Name you@example.com"`.
- **Superinvestors:** override or extend the fund list with `{"Fund name": "CIK"}`. Look up CIKs
  on EDGAR's company search.

## Adding a new sub-agent

Subclass `SourceAgent`, implement `fetch()` to return `Signal`s, and register the class in
`stockagent/sources/__init__.py`:

```python
class YouTubeAgent(SourceAgent):
    name = "youtube"
    requires_env = ("YOUTUBE_API_KEY",)

    async def fetch(self) -> list[Signal]:
        ...  # fetch, then: Signal(source="youtube", ticker=t, kind=SignalKind.SOCIAL, sentiment=..., text=...)
```

Add a default in `DEFAULT_SOURCES` in `config.py`, and the orchestrator will schedule it.

## Development

```bash
pytest -q
```

The tests use recorded sample payloads for every source, so they need no network access or API keys.
