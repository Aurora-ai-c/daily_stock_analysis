# -*- coding: utf-8 -*-
"""screening / fundamental 消费点与契约层 FundamentalRaw 的兼容契约测试。"""
import pytest
from data_provider.contracts import FundamentalRaw
from datetime import date


def test_fundamental_raw_dump_keeps_keys_for_consumers():
    fr = FundamentalRaw(report_date=date(2026, 6, 30), fiscal_period="Q2", market="cn",
                        total_assets=1.0, revenue=2.0, net_income=0.5)
    d = fr.model_dump()
    assert d["report_date"] == date(2026, 6, 30)  # 消费者按原 key 读取
    assert d["total_assets"] == 1.0
