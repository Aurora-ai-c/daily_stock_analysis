import pytest
import pandas as pd
from datetime import date
from data_provider.tushare_fetcher import TushareFetcher
from data_provider.contracts import Quote, Bar
from data_provider.realtime_types import UnifiedRealtimeQuote


@pytest.fixture
def fetcher():
    return TushareFetcher()


def test_to_quote_maps_fields(fetcher):
    old = UnifiedRealtimeQuote(code="600519", price=1700.0, currency="CNY")
    q = fetcher.to_quote(old)
    assert isinstance(q, Quote)
    assert q.tz == "Asia/Shanghai"


def test_to_bar_shape(fetcher):
    df = pd.DataFrame([{"date": "2026-08-14", "open": 1, "high": 2, "low": 0.5,
                        "close": 1.5, "volume": 100}])
    bars = fetcher.to_bar(df)
    assert bars[0].date == date(2026, 8, 14)