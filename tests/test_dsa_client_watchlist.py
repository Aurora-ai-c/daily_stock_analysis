# -*- coding: utf-8 -*-
"""watchlist 单元测试(已迁移至 src/services/watchlist):解析/归一/校验。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import src.services.watchlist as wl  # noqa: E402


def test_split_symbols_mixed_separators():
    assert wl.split_symbols("600519，600036, 00700\nUS.AAPL") == [
        "600519", "600036", "00700", "US.AAPL"]
    assert wl.split_symbols("") == []
    assert wl.split_symbols(None) == []


def test_normalize_symbol_us_case():
    assert wl.normalize_symbol("us.aapl") == "US.AAPL"
    assert wl.normalize_symbol("US.BrK.B") == "US.BRK.B"
    assert wl.normalize_symbol(" 600519 ") == "600519"


def test_validate_symbol_known_forms():
    assert wl.validate_symbol("600519") is None
    assert wl.validate_symbol("00700") is None
    assert wl.validate_symbol("sh600519") is None
    assert wl.validate_symbol("hk00700") is None
    assert wl.validate_symbol("US.AAPL") is None
    assert wl.validate_symbol("") is not None


def test_validate_symbol_rejects_garbage():
    assert wl.validate_symbol("茅台") is not None
    assert wl.validate_symbol("AAPL") is not None          # 缺 US. 前缀
    assert wl.validate_symbol("60-0519") is not None
    assert wl.validate_symbol("60051") is None             # 5 位按港股形态放行(宽松策略)


def test_parse_watchlist_dedupe_and_invalid():
    out = wl.parse_watchlist("600519, 600519, US.aapl, 茅台, 300750")
    assert out["items"] == ["600519", "US.AAPL", "300750"]
    assert out["duplicates"] == ["600519"]
    assert out["invalid"] == ["茅台"]


def test_parse_watchlist_empty():
    out = wl.parse_watchlist(",, ,")
    assert out == {"items": [], "invalid": [], "duplicates": []}


def test_join_roundtrip():
    items = ["600519", "00700", "US.AAPL"]
    assert wl.parse_watchlist(wl.join_symbols(items))["items"] == items
