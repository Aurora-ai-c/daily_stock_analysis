# data_provider/quote_derived.py
# -*- coding: utf-8 -*-
"""QuoteDerived 计算层:组合 Quote + Bar + FundamentalRaw 产出派生指标。"""
from __future__ import annotations

from typing import Optional

from data_provider.contracts import Bar, FundamentalRaw, Quote, QuoteDerived


class QuoteDerivedCalculator:
    def calculate(self, quote: Quote, bars: Optional[list[Bar]] = None,
                  fundamental: Optional[FundamentalRaw] = None) -> QuoteDerived:
        d = QuoteDerived()
        if bars:
            d.volume_ratio = self._volume_ratio(quote, bars)
        if quote.pre_close and quote.high is not None and quote.low is not None:
            d.amplitude = round((quote.high - quote.low) / quote.pre_close * 100, 2)
        if fundamental:
            d.pe_ratio = getattr(fundamental, "pe_ratio", None)
        return d

    def _volume_ratio(self, quote: Quote, bars: list[Bar]) -> Optional[float]:
        if quote.volume is None or len(bars) < 6:
            return None
        prev_5 = [b.volume for b in bars[-6:-1]]
        if not prev_5 or sum(prev_5) == 0:
            return None
        avg = sum(prev_5) / 5
        return round(quote.volume / avg, 2)