# -*- coding: utf-8 -*-
"""Tests for data-source health observability and transient-retry hardening."""
import time

import pandas as pd
import pytest

from data_provider.base import BaseFetcher, DataFetcherManager


class _FakeFetcher(BaseFetcher):
    name = "FakeA"
    priority = 1

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        return pd.DataFrame()

    def _normalize_data(self, df, stock_code):
        return df


class _FakeFetcherB(BaseFetcher):
    name = "FakeB"
    priority = 3

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        return pd.DataFrame()

    def _normalize_data(self, df, stock_code):
        return df


def _make_manager():
    DataFetcherManager.reset_daily_source_health()
    return DataFetcherManager(fetchers=[_FakeFetcher(), _FakeFetcherB()])


def test_get_data_source_health_structure_and_sort():
    mgr = _make_manager()
    health = mgr.get_data_source_health()
    assert health["total"] == 2
    assert health["summary"] == "ok"
    names = [s["name"] for s in health["sources"]]
    assert names == ["FakeA", "FakeB"]  # sorted by priority ascending
    assert health["sources"][0]["state"] == "closed"
    assert health["sources"][0]["available"] is True


def test_health_reflects_circuit_breaker_trip():
    mgr = _make_manager()
    fa = mgr._fetchers_by_name["FakeA"]
    # 连续失败达到阈值(3) -> 熔断
    for _ in range(3):
        mgr._record_daily_source_failure(fa, "cn", "transient ssl")
    health = mgr.get_data_source_health()
    a = next(s for s in health["sources"] if s["name"] == "FakeA")
    assert a["state"] == "open"
    # 仅 FakeA 熔断,FakeB 正常 -> degraded
    assert health["summary"] == "degraded"


def test_health_all_failed_marker():
    mgr = _make_manager()
    for f in mgr._fetchers:
        for _ in range(3):
            mgr._record_daily_source_failure(f, "cn", "all down")
    health = mgr.get_data_source_health()
    assert health["summary"] == "all_failed"
    assert health["tripped_count"] == 2


def test_transient_network_retry_then_success():
    mgr = _make_manager()
    calls = {"n": 0}

    class _Flaky:
        name = "flaky"

        def boom(self):
            calls["n"] += 1
            # 前两次为瞬态网络错误,第三次成功
            if calls["n"] < 3:
                import requests
                raise requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")
            return "ok"

    result = mgr._call_fetcher_method(_Flaky(), "boom")
    assert result == "ok"
    assert calls["n"] == 3


def test_transient_retry_gives_up_after_attempts():
    mgr = _make_manager()
    calls = {"n": 0}

    class _AlwaysDown:
        name = "down"

        def boom(self):
            calls["n"] += 1
            import requests
            raise requests.exceptions.ConnectionError("remote end closed")

    with pytest.raises(Exception):
        mgr._call_fetcher_method(_AlwaysDown(), "boom")
    # stop_after_attempt(3) -> 恰好 3 次
    assert calls["n"] == 3
