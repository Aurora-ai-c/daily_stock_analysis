import pytest
from datetime import date
import pandas as pd
from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.contracts import Quote, Bar, FundamentalRaw
from data_provider.realtime_types import UnifiedRealtimeQuote


@pytest.fixture
def fetcher():
    return AkshareFetcher()


class TestAkshareToQuote:
    def test_maps_core_fields(self, fetcher):
        old = UnifiedRealtimeQuote(code="600519", price=1700.0, change_pct=1.2,
                                   currency="CNY", market="cn")
        q = fetcher.to_quote(old)
        assert isinstance(q, Quote)
        assert q.price == 1700.0
        assert q.currency == "CNY"
        assert q.tz == "Asia/Shanghai"  # A 股固定时区

    def test_missing_fields_tolerated(self, fetcher):
        old = UnifiedRealtimeQuote(code="000001")
        q = fetcher.to_quote(old)
        assert q.bid is None and q.ask is None


class TestAkshareToBar:
    def test_bar_shape(self, fetcher):
        df = pd.DataFrame([
            {"date": "2026-08-14", "open": 1, "high": 2, "low": 0.5,
             "close": 1.5, "volume": 100, "amount": 150.0, "pct_chg": 5.0},
        ])
        bars = fetcher.to_bar(df)
        assert len(bars) == 1
        assert bars[0].date == date(2026, 8, 14)
        assert bars[0].close == 1.5


class TestAkshareToFundamental:
    def test_raw_shape(self, fetcher):
        fr = fetcher.to_fundamental({"total_assets": 1.0, "report_date": "2026-06-30",
                                     "fiscal_period": "Q2", "market": "cn"})
        assert isinstance(fr, FundamentalRaw)
        assert fr.report_date == date(2026, 6, 30)