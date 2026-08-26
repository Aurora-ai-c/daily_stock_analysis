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
                              "low": 0.5, "close": 1.5, "volume": 100}]), "fake_fetcher"


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

def test_collect_retains_bars_by_code():
    """I-2a:collector 保留 per-code Bar 契约供 probe 消费。"""
    import pandas as pd

    from src.services.pipeline.collector import collect

    class _Mgr:
        def get_daily_data(self, code, **kw):
            df = pd.DataFrame([
                {"date": f"2026-07-{i:02d}", "open": 1.0, "high": 2.0,
                 "low": 0.5, "close": 1.5, "volume": 100 + i, "pct_chg": 0.5}
                for i in range(1, 11)
            ])
            return (df, "fake")

    art = collect(["600519"], ["cn"], _Mgr())
    bars = art.bars_by_code["600519"]
    assert len(bars) == 10
    assert bars[0].close == 1.5 and bars[0].volume == 101
    assert bars[9].pct_chg == 0.5


def test_df_to_bars_tolerates_dirty_rows_and_missing_optional_cols():
    import pandas as pd

    from src.services.pipeline.collector import _df_to_bars

    df = pd.DataFrame([
        {"date": "2026-07-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
        {"date": "bad", "open": "x", "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
        {"date": None, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
    ])
    bars = _df_to_bars(df)
    assert len(bars) == 1
    assert bars[0].pct_chg is None and bars[0].amount is None
