# -*- coding: utf-8 -*-
"""Regression tests: 周/月线必须取足换算后的日线根数再本地重采样。

修复前：非美股通用路径把原始 ``days`` 直接传给 fetcher（仅美股路径使用了
换算后的 ``fetch_days``），周线只拿到 ``days`` 根日线，重采样后仅
days/5 根周线、月线仅 days/22 根，技术指标失真。
"""

from unittest.mock import patch

import pandas as pd

from data_provider.base import BaseFetcher, DataFetcherManager


def _daily_frame(n: int) -> pd.DataFrame:
    dates = pd.bdate_range("2026-06-01", periods=n)
    return pd.DataFrame(
        {
            "date": dates,
            "open": [1.0] * n,
            "high": [2.0] * n,
            "low": [0.5] * n,
            "close": [1.5] * n,
            "volume": [100] * n,
            "amount": [150.0] * n,
            "pct_chg": [0.0] * n,
        }
    )


class _RecordingFetcher(BaseFetcher):
    """Record the ``days`` kwarg the manager forwards for A-share routing."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.priority = 1
        self.received_days = None

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        raise NotImplementedError

    def _normalize_data(self, df, stock_code):
        raise NotImplementedError

    def get_daily_data(self, stock_code, start_date=None, end_date=None, days=30):
        self.received_days = days
        return _daily_frame(10)


class TestPeriodFetchDaysForwardedToGenericFetchers:
    @patch("data_provider.base.manager.record_provider_run_started")
    @patch("data_provider.base.manager.record_provider_run")
    def test_weekly_fetches_days_times_five(self, _record_run, _record_started) -> None:
        fetcher = _RecordingFetcher("AkshareFetcher")
        manager = DataFetcherManager(fetchers=[fetcher])

        df, source = manager.get_daily_data("600519", days=20, period="weekly")

        assert source == "AkshareFetcher"
        assert not df.empty
        assert fetcher.received_days == 100  # 20 周 × 5 交易日

    @patch("data_provider.base.manager.record_provider_run_started")
    @patch("data_provider.base.manager.record_provider_run")
    def test_monthly_fetches_days_times_twenty_two(self, _record_run, _record_started) -> None:
        fetcher = _RecordingFetcher("AkshareFetcher")
        manager = DataFetcherManager(fetchers=[fetcher])

        df, source = manager.get_daily_data("600519", days=3, period="monthly")

        assert source == "AkshareFetcher"
        assert not df.empty
        assert fetcher.received_days == 66  # 3 月 × 22 交易日

    @patch("data_provider.base.manager.record_provider_run_started")
    @patch("data_provider.base.manager.record_provider_run")
    def test_daily_keeps_original_days(self, _record_run, _record_started) -> None:
        fetcher = _RecordingFetcher("AkshareFetcher")
        manager = DataFetcherManager(fetchers=[fetcher])

        manager.get_daily_data("600519", days=60)

        assert fetcher.received_days == 60
