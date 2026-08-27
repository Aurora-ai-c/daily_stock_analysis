# -*- coding: utf-8 -*-
"""AlphaEvo 端到端验证:用合成 OHLCV 喂 run_probe,证明引擎在"有数据"时真出信号。

背景:此前所有测试要么 mock 掉 run_probe,要么喂空 frame,导致 AlphaEvo 真实计算路径
(策略 YAML → BacktestEngine.signal_at_last_bar)从未被端到端验证。本文件用合成行情
数据驱动真实策略集,断言引擎确实被调用并返回信号结构,而不是因缺数据而整体为空。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

STRATEGY_DIR = Path(__file__).resolve().parent.parent / "strategy_signals"
PROBE = __import__("src.strategy_signal_probe", fromlist=["x"])


def _make_manager():
    """返回一个假 DataFetcherManager:get_daily_data 返回合成 OHLCV,get_stock_name 返回名字。"""

    class _FakeManager:
        def get_stock_name(self, code, allow_realtime=False):
            return f"测试股{code}"

        def get_daily_data(self, code, start=None, end=None, max_days=250):
            n = max(int(max_days or 250), 120)
            # 带趋势 + 噪声的合成收盘价,确保指标有足够变化
            rng = np.random.default_rng(abs(hash(code)) % (2**32))
            drift = np.linspace(0, 30, n)
            noise = rng.normal(0, 1.0, n).cumsum()
            close = 100 + drift + noise
            close = np.maximum(close, 1.0)
            high = close + rng.uniform(0, 1.5, n)
            low = close - rng.uniform(0, 1.5, n)
            opn = close + rng.uniform(-1.0, 1.0, n)
            volume = rng.integers(1_000_000, 5_000_000, n).astype("int64")
            dates = pd.date_range(end="2025-12-31", periods=n, freq="B")
            df = pd.DataFrame({
                "date": dates,
                "open": opn,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            })
            return df, "synthetic"

    return _FakeManager()


def test_alphaevo_produces_signals_on_synthetic_data():
    """核心断言:有数据时 run_probe 不为空,且对每个启用的策略都返回信号结构。"""
    mgr = _make_manager()
    result = asyncio.run(
        PROBE.run_probe(
            config_dir=STRATEGY_DIR,
            data_manager=mgr,
            symbols=["600519", "000001"],
            timeout_seconds=120,
        )
    )
    symbols = result.get("symbols", {})
    assert symbols, "run_probe 返回空 symbols —— AlphaEvo 路径未产出任何内容"

    total_signals = 0
    for code, entry in symbols.items():
        # 关键:不是因取数失败而空
        assert entry.get("error") != "fetch_failed_or_empty", f"{code} 取数失败,数据层未注入"
        # 信号嵌套在 groups[group].signals[sid]
        for gname, grp in entry.get("groups", {}).items():
            sigs = grp.get("signals", {})
            total_signals += len(sigs)
            for sid, sig in sigs.items():
                # 引擎返回的信号结构必须含这些规范化字段
                assert "triggered" in sig and "entry_price" in sig and "reason" in sig, \
                    f"{code}/{gname}/{sid} 信号结构不完整: {sig}"

    assert total_signals > 0, "引擎被调用但零信号 —— 需检查策略 YAML 与 AlphaEvo 适配"


def test_alphaevo_no_fetch_issue_with_data():
    """对照:有数据时不应出现 fetch_issues 里的取数失败项。"""
    mgr = _make_manager()
    result = asyncio.run(
        PROBE.run_probe(
            config_dir=STRATEGY_DIR,
            data_manager=mgr,
            symbols=["600519"],
            timeout_seconds=120,
        )
    )
    issues = result.get("data_issues", {})
    assert "600519" not in issues, f"600519 被记为数据不足: {issues.get('600519')}"
