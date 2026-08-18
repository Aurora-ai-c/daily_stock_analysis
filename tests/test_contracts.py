# tests/test_contracts.py
import pytest
from datetime import date
from data_provider.contracts import Bar, Quote, QuoteDerived, FundamentalRaw, FundamentalDerived


class TestQuoteRaw:
    def test_accepts_minimal_fields(self):
        q = Quote(code="600519", price=1700.0)
        assert q.price == 1700.0
        assert q.bid is None  # 缺省容忍

    def test_legacy_compat_maps_fields(self):
        q = Quote(code="600519", price=1700.0, currency="CNY", market="cn", is_stale=False)
        old = q.legacy_compat()
        assert old.code == "600519"
        assert old.price == 1700.0
        assert old.currency == "CNY"


class TestBar:
    def test_requires_date_and_ohlc(self):
        b = Bar(date=date(2026, 8, 15), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)
        assert b.close == 1.5
        assert b.turnover_rate is None


class TestFundamentalRaw:
    def test_requires_report_period(self):
        fr = FundamentalRaw(report_date=date(2026, 6, 30), fiscal_period="Q2", market="cn",
                            total_assets=1.0, revenue=1.0, net_income=1.0)
        assert fr.fiscal_period == "Q2"


class TestQuoteDerived:
    def test_all_optional(self):
        d = QuoteDerived()
        assert d.pe_ratio is None