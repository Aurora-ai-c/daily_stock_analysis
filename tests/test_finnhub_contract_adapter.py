import pytest
import pandas as pd
from datetime import date
from data_provider.finnhub_fetcher import FinnhubFetcher
from data_provider.contracts import Quote, Bar, FundamentalRaw
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


class TestFinnhubToFundamental:
    def test_raw_shape(self, fetcher):
        fr = fetcher.to_fundamental({"total_assets": 1.0, "report_date": "2026-06-30",
                                     "fiscal_period": "Q2"})
        assert isinstance(fr, FundamentalRaw)
        assert fr.report_date == date(2026, 6, 30)
        assert fr.fiscal_period == "Q2"
        assert fr.total_assets == 1.0
        assert fr.market == "us"  # 美股源缺省市场

    def test_missing_report_date_returns_none(self, fetcher):
        assert fetcher.to_fundamental({"total_assets": 1.0}) is None