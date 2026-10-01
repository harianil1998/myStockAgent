import pytest

from stockagent.store import Store
from stockagent.tickers import TickerUniverse

SEC_SAMPLE = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "2": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
    "3": {"cik_str": 2488, "ticker": "AMD", "title": "ADVANCED MICRO DEVICES INC"},
    "4": {"cik_str": 1, "ticker": "GME", "title": "GameStop Corp."},
    "5": {"cik_str": 2, "ticker": "ALL", "title": "Allstate Corp"},
    "6": {"cik_str": 3, "ticker": "OXY", "title": "OCCIDENTAL PETROLEUM CORP"},
    "7": {"cik_str": 4, "ticker": "PLTR", "title": "Palantir Technologies Inc."},
    "8": {"cik_str": 5, "ticker": "IT", "title": "Gartner Inc"},
}


@pytest.fixture
def universe():
    return TickerUniverse.from_sec_json(SEC_SAMPLE)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")
