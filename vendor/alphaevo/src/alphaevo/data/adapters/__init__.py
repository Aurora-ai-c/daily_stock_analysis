"""Data adapters for various market data sources."""

from __future__ import annotations

from typing import Any

__all__ = [
    "AdanosSentimentAdapter",
    "AkShareAdapter",
    "DSAAdapter",
    "TencentAshareAdapter",
    "YFinanceAdapter",
]


def __getattr__(name: str) -> Any:
    """Lazy imports to avoid pulling in optional dependencies at package level."""
    if name == "YFinanceAdapter":
        from alphaevo.data.adapters.yfinance import YFinanceAdapter

        return YFinanceAdapter
    if name == "AkShareAdapter":
        from alphaevo.data.adapters.akshare import AkShareAdapter

        return AkShareAdapter
    if name == "TencentAshareAdapter":
        from alphaevo.data.adapters.tencent import TencentAshareAdapter

        return TencentAshareAdapter
    if name == "DSAAdapter":
        from alphaevo.data.adapters.dsa import DSAAdapter

        return DSAAdapter
    if name == "AdanosSentimentAdapter":
        from alphaevo.data.adapters.adanos import AdanosSentimentAdapter

        return AdanosSentimentAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
