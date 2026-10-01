import time
from datetime import datetime, timedelta, timezone

from stockagent.models import SignalKind
from stockagent.sources.ark import ArkAgent, parse_holdings
from stockagent.sources.congress import CongressAgent, range_floor
from stockagent.sources.discord import DiscordAgent
from stockagent.sources.insiders import InsiderAgent, parse_form4
from stockagent.sources.market import MarketAgent, compute_stats
from stockagent.sources.news import NewsAgent
from stockagent.sources.reddit import RedditAgent
from stockagent.sources.stocktwits import StockTwitsAgent
from stockagent.sources.superinvestors import SuperinvestorAgent, diff_holdings, parse_info_table
from stockagent.sources.twitter import TwitterAgent


def mk(cls, universe, store, **opts):
    return cls(opts, universe, store, watchlist=["NVDA"])


def test_reddit_parse(universe, store):
    data = {"data": {"children": [
        {"data": {"title": "NVDA calls printing 🚀", "selftext": "bullish into earnings", "score": 500,
                  "num_comments": 120, "permalink": "/r/wsb/x", "author": "u1", "created_utc": time.time()}},
        {"data": {"title": "Weekend thread", "selftext": "", "score": 3, "num_comments": 1,
                  "permalink": "/r/wsb/y", "author": "u2", "created_utc": time.time()}},
    ]}}
    sigs = mk(RedditAgent, universe, store).parse(data, "wallstreetbets")
    assert [s.ticker for s in sigs] == ["NVDA"]
    assert sigs[0].sentiment > 0 and sigs[0].weight > 3
    assert sigs[0].source == "reddit/r/wallstreetbets"


def test_stocktwits_uses_user_labels(universe, store):
    data = {"messages": [
        {"id": 1, "body": "$TSLA whatever", "symbols": [{"symbol": "TSLA"}], "created_at": "2026-10-01T12:00:00Z",
         "user": {"username": "a", "followers": 1000}, "entities": {"sentiment": {"basic": "Bearish"}}},
        {"id": 2, "body": "$BTC.X pump", "symbols": [{"symbol": "BTC.X"}], "created_at": "2026-10-01T12:00:00Z",
         "user": {}, "entities": {}},
    ]}
    sigs = mk(StockTwitsAgent, universe, store).parse(data)
    assert len(sigs) == 1 and sigs[0].ticker == "TSLA" and sigs[0].sentiment == -0.8


def test_twitter_parse_and_queries(universe, store):
    agent = mk(TwitterAgent, universe, store, accounts=["@trader1", "trader2"])
    qs = agent.build_queries()
    assert "from:trader1 OR from:trader2" in qs[0] and "$NVDA" in qs[1]
    data = {
        "data": [{"id": "9", "text": "Adding $AMD here, breakout", "author_id": "u",
                  "created_at": "2026-10-01T10:00:00.000Z", "entities": {"cashtags": [{"tag": "AMD"}]},
                  "public_metrics": {"like_count": 50, "retweet_count": 10}}],
        "includes": {"users": [{"id": "u", "username": "trader1", "public_metrics": {"followers_count": 100000}}]},
    }
    sigs = agent.parse(data)
    assert sigs[0].ticker == "AMD" and sigs[0].url == "https://x.com/trader1/status/9" and sigs[0].sentiment > 0


def test_discord_reads_embeds(universe, store):
    msgs = [{"id": "100", "content": "", "author": {"username": "AlertBot"}, "timestamp": "2026-10-01T13:30:00+00:00",
             "embeds": [{"title": "Unusual options", "description": "$PLTR calls sweep, bullish",
                         "fields": [{"name": "Strike", "value": "30C"}]}]}]
    sigs = mk(DiscordAgent, universe, store).parse(msgs, "555")
    assert sigs[0].ticker == "PLTR" and sigs[0].sentiment > 0 and "555" in sigs[0].source


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Tesla shares plunge after delivery miss</title><link>https://ex.com/1</link>
<description>TSLA fell sharply.</description><pubDate>Wed, 01 Oct 2026 12:00:00 GMT</pubDate></item>
<item><title>Fed holds rates</title><link>https://ex.com/2</link><description>Macro.</description></item>
</channel></rss>"""


def test_news_parse(universe, store):
    sigs = mk(NewsAgent, universe, store).parse(RSS, "https://ex.com/rss")
    assert [s.ticker for s in sigs] == ["TSLA"]
    assert sigs[0].kind == SignalKind.NEWS and sigs[0].sentiment < 0
    assert sigs[0].timestamp == datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def _info_table(rows):
    items = "".join(
        f"""<infoTable><nameOfIssuer>{n}</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>{c}</cusip>
        <value>{v}</value><shrsOrPrnAmt><sshPrnamt>{s}</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
        {'<putCall>Put</putCall>' if pc else ''}</infoTable>""" for n, c, v, s, pc in rows)
    return f'<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">{items}</informationTable>'


def test_13f_diff(universe, store):
    prev = parse_info_table(_info_table([("APPLE INC", "037833100", 900, 1000, False),
                                         ("GAMESTOP CORP", "36467W109", 100, 500, False)]))
    cur = parse_info_table(_info_table([("APPLE INC", "037833100", 1500, 1500, False),
                                        ("OCCIDENTAL PETROLEUM CORP", "674599105", 500, 800, False),
                                        ("NVIDIA CORP", "67066G104", 50, 10, True)]))  # put: ignored
    changes = {c["name"]: c["action"] for c in diff_holdings(prev, cur)}
    assert changes == {"APPLE INC": "ADD", "OCCIDENTAL PETROLEUM CORP": "NEW", "GAMESTOP CORP": "EXIT"}
    sigs = mk(SuperinvestorAgent, universe, store).to_signals(
        "Berkshire", diff_holdings(prev, cur), datetime.now(timezone.utc), "u")
    by = {s.ticker: s for s in sigs}
    assert by["OXY"].sentiment > by["AAPL"].sentiment > 0 > by["GME"].sentiment
    assert all(s.kind == SignalKind.SMART_MONEY for s in sigs)


ARK_CSV = """date,fund,company,ticker,cusip,shares,"market value ($)","weight (%)"
{d},ARKK,TESLA INC,TSLA,88160R101,"{tsla}","$1","{w}%"
{d},ARKK,PALANTIR,PLTR,69608A108,"{pltr}","$1","3.00%"
"""


def test_ark_diff(universe, store):
    prev = parse_holdings(ARK_CSV.format(d="09/30/2026", tsla="1,000,000", pltr="500,000", w="10.00"))
    cur = parse_holdings(ARK_CSV.format(d="10/01/2026", tsla="1,100,000", pltr="500,000", w="11.00"))
    del cur["PLTR"]
    sigs = mk(ArkAgent, universe, store).diff("ARKK", prev, cur)
    actions = {s.ticker: s.extra["action"] for s in sigs}
    assert actions == {"TSLA": "BUY", "PLTR": "EXIT"}


FORM4 = """<ownershipDocument><issuer><issuerTradingSymbol>amd</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerName>Su Lisa</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle>
</reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction>
<transactionDate><value>2026-09-29</value></transactionDate>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>10000</value></transactionShares>
<transactionPricePerShare><value>150.00</value></transactionPricePerShare></transactionAmounts>
</nonDerivativeTransaction>
<nonDerivativeTransaction><transactionCoding><transactionCode>M</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>5</value></transactionShares></transactionAmounts>
</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""


def test_form4_purchase(universe, store):
    txs = parse_form4(FORM4)
    assert txs[0]["ticker"] == "AMD" and txs[0]["value"] == 1_500_000 and txs[0]["role"] == "Director, CEO"
    sigs = mk(InsiderAgent, universe, store).to_signals(txs, "u")
    assert len(sigs) == 1  # option exercise (M) is ignored
    assert sigs[0].sentiment > 0.5 and sigs[0].kind == SignalKind.INSIDER and "BOUGHT" in sigs[0].text


def test_congress(universe, store):
    assert range_floor("$15,001 - $50,000") == 15001
    rows = [{"Representative": "Jane Doe", "Ticker": "NVDA", "Transaction": "Purchase", "Range": "$250,001 - $500,000",
             "ReportDate": "2026-09-28", "House": "Senate"},
            {"Representative": "X", "Ticker": "", "Transaction": "Purchase"}]
    sigs = mk(CongressAgent, universe, store).parse(rows)
    assert len(sigs) == 1 and sigs[0].kind == SignalKind.POLITICIAN and sigs[0].weight > 3


def _chart(closes, vols):
    return {"chart": {"result": [{"indicators": {"quote": [{"close": closes, "volume": vols}]}}]}}


def test_market_anomaly(universe, store):
    closes = [100.0] * 25 + [108.0]
    vols = [1_000_000] * 25 + [3_500_000]
    s = compute_stats(_chart(closes, vols))
    assert s["chg_1d"] == 8.0 and s["vol_ratio"] == 3.5
    sigs = mk(MarketAgent, universe, store).to_signals({"NVDA": s, "AAPL": compute_stats(_chart([100.0] * 26, [1] * 26))})
    assert [x.ticker for x in sigs] == ["NVDA"] and sigs[0].sentiment > 0
    assert compute_stats({"chart": {"result": None}}) is None


async def test_failing_source_is_isolated(universe, store, monkeypatch):
    agent = mk(TwitterAgent, universe, store)
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    assert await agent.run_once() == []
    assert "missing env" in agent.last_error

    class Boom(RedditAgent):
        async def fetch(self):
            raise RuntimeError("down")
    b = mk(Boom, universe, store)
    assert await b.run_once() == [] and "down" in b.last_error
