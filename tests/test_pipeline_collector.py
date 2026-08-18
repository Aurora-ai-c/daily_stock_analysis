# -*- coding: utf-8 -*-
import pytest
from src.services.pipeline.collector import (
    collect, CollectorHardFailError,
)


class _FakeManager:
    def __init__(self, ok_markets):
        self.ok_markets = set(ok_markets)

    def get_daily_data(self, code, **kw):
        market = "us" if "US" in code else "cn"
        if market not in self.ok_markets:
            raise RuntimeError("source unavailable")
        import pandas as pd
        return pd.DataFrame([{"date": "2026-08-14", "open": 1, "high": 2,
                              "low": 0.5, "close": 1.5, "volume": 100}])


class TestCollector:
    def test_partial_failure_marks_missing_markets(self):
        art = collect(["600519", "US.AAPL"], markets=["cn", "us"], manager=_FakeManager({"cn"}))
        assert art.missing_markets == ["us"]
        assert art.rows["cn"] >= 1

    def test_full_failure_raises_hard_fail(self):
        with pytest.raises(CollectorHardFailError):
            collect(["600519"], markets=["cn"], manager=_FakeManager(set()))

    def test_artifact_schema_version(self):
        art = collect(["600519"], markets=["cn"], manager=_FakeManager({"cn"}))
        assert art.schema_version == 1
