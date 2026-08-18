# -*- coding: utf-8 -*-
import pytest
from data_provider.specs import load_fetcher_specs, DEFAULT_REGISTRY_PATH


def test_registry_has_all_sources():
    specs = load_fetcher_specs(DEFAULT_REGISTRY_PATH)
    names = {s.name for s in specs}
    assert {"akshare", "tushare", "yfinance", "finnhub", "tencent", "pytdx",
            "efinance", "baostock", "longbridge", "tickflow",
            "alphavantage", "tw_institutional"} <= names


def test_v1_sources_enabled():
    specs = {s.name: s for s in load_fetcher_specs(DEFAULT_REGISTRY_PATH)}
    assert specs["akshare"].enabled and specs["yfinance"].enabled


def test_v1_others_disabled_by_default():
    specs = {s.name: s for s in load_fetcher_specs(DEFAULT_REGISTRY_PATH)}
    assert specs["tencent"].enabled is False