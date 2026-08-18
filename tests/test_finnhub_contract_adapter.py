import pytest
import pandas as pd
from datetime import date
from data_provider.finnhub_fetcher import FinnhubFetcher
from data_provider.contracts import Quote, Bar
from data_provider.realtime_types import UnifiedRealtimeQuote


@pytest.fixture
def fetcher():
    return FinnhubFetcher()


def test_to_quote_maps_fields(fetcher):
    old = UnifiedRealtimeQuote(code="AAPL", price=200.0, currency="USD")
    q = fetcher.to_quote(old)
    assert isinstance(q, Quote)
    assert q.tz == "America/New_York"


def test_to_bar_shape(fetcher):
    df = pd.DataFrame([{"date": "2026-08-14", "open": 1, "high": 2, "low": 0.5,
                        "close": 1.5, "volume": 100}])
    bars = fetcher.to_bar(df)
    assert bars[0].date == date(2026, 8, 14)