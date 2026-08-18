# -*- coding: utf-8 -*-
"""统一数据契约层(pydantic v2):raw/derived 分层,缺省容忍。"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote


class Quote(BaseModel):
    """fetcher 直接产出的实时行情(raw 层)。"""
    model_config = ConfigDict(populate_by_name=True)

    code: str
    name: str = ""
    price: Optional[float] = None
    open_price: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    pre_close: Optional[float] = None
    volume: Optional[int] = None
    amount: Optional[float] = None
    change_pct: Optional[float] = Field(None, description="涨跌幅(%),基准为昨收")
    change_amount: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    tz: Optional[Literal["Asia/Shanghai", "America/New_York", "UTC"]] = None
    currency: Optional[str] = None
    market: Optional[str] = None
    fetched_at: Optional[str] = None
    provider_timestamp: Optional[str] = None
    is_stale: Optional[bool] = None
    stale_seconds: Optional[int] = None
    fallback_from: Optional[str] = None
    data_quality: Optional[str] = None
    missing_fields: Optional[list[str]] = None

    def legacy_compat(self, source: RealtimeSource = RealtimeSource.FALLBACK) -> UnifiedRealtimeQuote:
        """转回旧 UnifiedRealtimeQuote,兼容迁移期调用方。"""
        return UnifiedRealtimeQuote(
            code=self.code, name=self.name, source=source,
            fetched_at=self.fetched_at, provider_timestamp=self.provider_timestamp,
            is_stale=self.is_stale, stale_seconds=self.stale_seconds,
            fallback_from=self.fallback_from, market=self.market, currency=self.currency,
            data_quality=self.data_quality, missing_fields=self.missing_fields,
            price=self.price, change_pct=self.change_pct, change_amount=self.change_amount,
            volume=self.volume, amount=self.amount,
            open_price=self.open_price, high=self.high, low=self.low, pre_close=self.pre_close,
        )


class Bar(BaseModel):
    """日线契约:仅 OHLCV + amount + pct_chg + turnover_rate,派生指标由调用方计算。"""
    model_config = ConfigDict(populate_by_name=True)

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: Optional[float] = None
    pct_chg: Optional[float] = None
    turnover_rate: Optional[float] = None


class FundamentalRaw(BaseModel):
    """三表关键科目(report_date/fiscal_period 必填)。"""
    model_config = ConfigDict(populate_by_name=True)

    report_date: date
    fiscal_period: Literal["Q1", "Q2", "Q3", "Q4", "FY"]
    market: str
    total_assets: Optional[float] = None
    total_liabilities: Optional[float] = None
    total_equity: Optional[float] = None
    revenue: Optional[float] = None
    net_income: Optional[float] = None
    operating_cashflow: Optional[float] = None
    investing_cashflow: Optional[float] = None
    financing_cashflow: Optional[float] = None
    gross_margin: Optional[float] = None
    dividend_yield: Optional[float] = None
    industry: Optional[str] = None


class FundamentalDerived(BaseModel):
    """派生指标,分三组标注依赖源。"""
    model_config = ConfigDict(populate_by_name=True)

    roe: Optional[float] = Field(None, description="依赖:纯基本面")
    dividend_yield_derived: Optional[float] = Field(None, description="依赖:纯基本面")
    pe_ratio: Optional[float] = Field(None, description="依赖:跨切股价")
    pb_ratio: Optional[float] = Field(None, description="依赖:跨切股价")
    high_52w: Optional[float] = Field(None, description="依赖:历史窗口")
    low_52w: Optional[float] = Field(None, description="依赖:历史窗口")


class QuoteDerived(BaseModel):
    """行情派生指标,由 QuoteDerivedCalculator 组合 Quote+Bar+FundamentalRaw 产出。"""
    model_config = ConfigDict(populate_by_name=True)

    volume_ratio: Optional[float] = None
    turnover_rate: Optional[float] = None
    amplitude: Optional[float] = None
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    total_mv: Optional[float] = None
    circ_mv: Optional[float] = None
    change_60d: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None