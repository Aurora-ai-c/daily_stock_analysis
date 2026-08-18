# -*- coding: utf-8 -*-
"""步骤 1:数据采集。hard-fail 仅全市场无数据;部分失败 per-market 降级。"""
from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel

from data_provider.base import DataFetcherManager


class CollectorHardFailError(RuntimeError):
    pass


class CollectorArtifact(BaseModel):
    fetchers_used: list[str] = []
    rows: dict[str, int] = {}
    missing_markets: list[str] = []
    latency: float = 0.0
    schema_version: Literal[1] = 1


def collect(stock_codes: list[str], markets: list[str],
            manager: DataFetcherManager) -> CollectorArtifact:
    start = time.monotonic()
    rows: dict[str, int] = {}
    missing: list[str] = []
    for market in markets:
        codes = [c for c in stock_codes if _market_of(c) == market] or stock_codes
        try:
            total = 0
            for code in codes:
                df = manager.get_daily_data(code)
                total += 0 if df is None else len(df)
            rows[market] = total
        except Exception:  # noqa: BLE001
            missing.append(market)
    if not rows:
        raise CollectorHardFailError(f"no data for any market: {markets}")
    return CollectorArtifact(fetchers_used=["registry"], rows=rows,
                             missing_markets=missing,
                             latency=round(time.monotonic() - start, 3))


def _market_of(code: str) -> str:
    upper = code.upper()
    if upper.startswith(("US.", "NASDAQ", "NYSE")) or not upper[:2].isdigit():
        return "us"
    return "cn"
