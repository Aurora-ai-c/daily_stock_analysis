# -*- coding: utf-8 -*-
"""Agent tools 与契约层 Quote 的兼容契约测试。"""
import pytest
from data_provider.contracts import Quote


def test_quote_legacy_compat_supports_getattr_reads():
    q = Quote(code="600519", price=1700.0, name="贵州茅台", currency="CNY")
    old = q.legacy_compat()
    assert getattr(old, "price", None) == 1700.0
    assert getattr(old, "name", "") == "贵州茅台"
