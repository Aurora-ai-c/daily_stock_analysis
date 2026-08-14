# -*- coding: utf-8 -*-
"""Strategy Lab API schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StrategyListItem(BaseModel):
    strategy_id: str
    family: str = ""
    version: int = 1
    name: str = ""
    description: str = ""
    warmup_days: int = 120
    enabled: bool = True
    disable_reason: str = ""
    tie_rule: str = "conservative"


class StrategyListResponse(BaseModel):
    items: List[StrategyListItem] = Field(default_factory=list)


class SignalPreviewRequest(BaseModel):
    symbols: Optional[List[str]] = Field(None, description="覆盖自选股列表（默认取配置 watchlist）")


class SignalGroupOut(BaseModel):
    level: str = ""
    vote_ratio: float = 0.0
    triggered: int = 0
    total: int = 0
    signals: Dict[str, Any] = Field(default_factory=dict)


class SignalSymbolOut(BaseModel):
    name: str = ""
    st: bool = False
    as_of_date: str = ""
    groups: Dict[str, SignalGroupOut] = Field(default_factory=dict)


class SignalPreviewResponse(BaseModel):
    generated_at: str = ""
    as_of_date: Optional[str] = None
    strategies_loaded: int = 0
    groups: Dict[str, List[str]] = Field(default_factory=dict)
    symbols: Dict[str, SignalSymbolOut] = Field(default_factory=dict)
    data_issues: Dict[str, str] = Field(default_factory=dict)
    strategy_errors: List[Dict[str, str]] = Field(default_factory=list)
    limit_basis: str = "prev_close"
    probe_version: int = 1


class LabBacktestRequest(BaseModel):
    strategy_id: str = Field(..., description="策略 ID")
    symbols: Optional[List[str]] = Field(None, description="覆盖股票列表")
    days: int = Field(360, ge=30, le=1095, description="回测窗口（自然日）")


class LabBacktestResponse(BaseModel):
    strategy_id: str
    name: str = ""
    date_range: Dict[str, Any] = Field(default_factory=dict)
    symbols: List[str] = Field(default_factory=list)
    fetch_issues: Dict[str, str] = Field(default_factory=dict)
    overall: Dict[str, Any] = Field(default_factory=dict)
    confidence_score: float = 0.0
    per_symbol: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    by_regime: List[Dict[str, Any]] = Field(default_factory=list)


class EvolveRequest(BaseModel):
    strategy_id: str = Field(..., description="要进化的策略 ID")
    method: str = Field("hybrid", pattern="^(llm|param_search|hybrid)$", description="进化方法")
    rounds: int = Field(1, ge=1, le=5, description="进化轮数")
    samples: int = Field(3, ge=1, le=10, description="每轮样本股票数")


class EvolveResponse(BaseModel):
    family: str
    source_strategy_id: str
    result_strategy_id: str
    version: int
    exported_yaml: Optional[str] = None
    report_dir: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""


class PublishRequest(BaseModel):
    strategy_ids: List[str] = Field(..., min_length=1, description="要发布的策略 ID 列表")


class PublishItem(BaseModel):
    strategy_id: str
    file: str
    committed: bool
    pushed: bool


class PublishResponse(BaseModel):
    items: List[PublishItem] = Field(default_factory=list)
