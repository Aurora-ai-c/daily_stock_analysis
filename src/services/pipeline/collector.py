# -*- coding: utf-8 -*-
"""步骤 1:数据采集。hard-fail 仅全市场无数据;部分失败 per-market 降级。

v2 接线:保留 per-code 日线(Bar 契约)供 probe 消费,不再只计数。
"""
from __future__ import annotations

import time
from typing import Literal, Optional

import pandas as pd
from pydantic import BaseModel

from data_provider.base import DataFetcherManager
from data_provider.contracts import Bar


class CollectorHardFailError(RuntimeError):
    pass


class CollectorArtifact(BaseModel):
    fetchers_used: list[str] = []
    rows: dict[str, int] = {}
    missing_markets: list[str] = []
    latency: float = 0.0
    bars_by_code: dict[str, list[Bar]] = {}
    schema_version: Literal[1] = 1


def collect(stock_codes: list[str], markets: list[str],
            manager: DataFetcherManager) -> CollectorArtifact:
    start = time.monotonic()
    rows: dict[str, int] = {}
    missing: list[str] = []
    bars_by_code: dict[str, list[Bar]] = {}
    for market in markets:
        codes = [c for c in stock_codes if _market_of(c) == market] or stock_codes
        try:
            total = 0
            for code in codes:
                df, _ = manager.get_daily_data(code)
                total += 0 if df is None else len(df)
                bars = _df_to_bars(df)
                if bars:
                    bars_by_code[code] = bars
            rows[market] = total
        except Exception:  # noqa: BLE001
            missing.append(market)
    if not rows:
        raise CollectorHardFailError(f"no data for any market: {markets}")
    return CollectorArtifact(fetchers_used=["registry"], rows=rows,
                             missing_markets=missing,
                             latency=round(time.monotonic() - start, 3),
                             bars_by_code=bars_by_code)


def _df_to_bars(df: Optional[pd.DataFrame]) -> list[Bar]:
    """DataFrame → Bar 契约列表;列名大小写不敏感、缺失列容忍(pct_chg/amount 可选)。"""
    if df is None or df.empty:
        return []
    cols = {str(c).lower(): c for c in df.columns}

    def col(*names: str) -> Optional[str]:
        for n in names:
            if n in cols:
                return cols[n]
        return None

    d = col("date", "trade_date", "datetime")
    o = col("open")
    h = col("high")
    l = col("low")
    c = col("close")
    v = col("volume", "vol")
    if not all((d, o, h, l, c, v)):
        return []
    p = col("pct_chg", "change_pct")
    a = col("amount")
    bars: list[Bar] = []
    for _, r in df.iterrows():
        try:
            bars.append(Bar(
                date=str(r[d]),
                open=float(r[o]),
                high=float(r[h]),
                low=float(r[l]),
                close=float(r[c]),
                volume=int(r[v]),
                pct_chg=(float(r[p]) if p is not None and pd.notna(r[p]) else None),
                amount=(float(r[a]) if a is not None and pd.notna(r[a]) else None),
            ))
        except Exception:  # noqa: BLE001 - 单行脏数据跳过
            continue
    return bars


def _market_of(code: str) -> str:
    upper = code.upper()
    if upper.startswith(("US.", "NASDAQ", "NYSE")) or not upper[:2].isdigit():
        return "us"
    return "cn"
