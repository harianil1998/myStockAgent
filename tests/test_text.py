from stockagent.sentiment import score_text
from stockagent.tickers import TickerUniverse, normalize_issuer


def test_cashtags_and_bare_symbols(universe):
    assert universe.extract("Loading $NVDA calls and some AMD too") == ["NVDA", "AMD"]


def test_stopwords_are_not_tickers(universe):
    # ALL and IT are real tickers but almost always plain English.
    assert universe.extract("ALL IN on IT, this is it") == []
    # ...unless explicitly cashtagged.
    assert universe.extract("$ALL looks cheap") == ["ALL"]


def test_unknown_cashtags_dropped_with_universe(universe):
    assert universe.extract("$FAKEZ to the moon") == []


def test_no_universe_accepts_cashtags_only():
    u = TickerUniverse()
    assert u.extract("$XYZ and NVDA") == ["XYZ"]


def test_issuer_lookup(universe):
    assert normalize_issuer("Apple Inc.") == "APPLE"
    assert universe.lookup_issuer("APPLE INC") == "AAPL"
    assert universe.lookup_issuer("OCCIDENTAL PETE CORP") is None  # abbreviations don't fuzzy-match
    assert universe.lookup_issuer("Occidental Petroleum Corporation") == "OXY"


def test_sentiment_direction():
    assert score_text("Buying calls, this is going to moon 🚀") > 0.3
    assert score_text("Bought puts, earnings miss and a downgrade, dump incoming") < -0.3
    assert score_text("The meeting is on Tuesday") == 0.0


def test_sentiment_negation():
    assert score_text("not bullish") < 0
