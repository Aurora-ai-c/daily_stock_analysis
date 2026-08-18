# -*- coding: utf-8 -*-
"""pipeline 与契约层 Quote 的兼容契约测试(legacy 适配层)。"""
import pytest
from data_provider.contracts import Quote
from src.core.pipeline import _to_legacy_quote
from data_provider.realtime_types import UnifiedRealtimeQuote


def test_helper_passes_legacy_through():
    old = UnifiedRealtimeQuote(code="600519")
    assert _to_legacy_quote(old) is old


def test_helper_converts_new_quote():
    q = Quote(code="600519", price=1700.0)
    out = _to_legacy_quote(q)
    assert isinstance(out, UnifiedRealtimeQuote)
    assert out.price == 1700.0
