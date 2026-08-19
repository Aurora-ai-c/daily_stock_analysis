# -*- coding: utf-8 -*-
"""步骤 3:交叉验证。三路输入(probe/llm/backtest)投票,结构化 resolution。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from src.services.pipeline.probe import ProbeArtifact, ProbeSignal


class ValidatedSignal(BaseModel):
    source: Literal["probe", "llm", "backtest"]
    code: str
    direction: str
    confidence: float
    confidence_label: Literal["confirmed", "unverified", "confirmed_via_majority",
                              "rejected_via_majority", "tie_pending_review"]
    timestamp: str = ""


class CrossValidatorArtifact(BaseModel):
    confirm: list[str] = []
    conflict: list[str] = []
    resolution: Optional[Literal["confirmed_via_majority", "rejected_via_majority",
                                "tie_pending_review"]] = None
    signals: list[ValidatedSignal] = []
    schema_version: Literal[1] = 1


def cross_validate(probe_art: Optional[ProbeArtifact], llm_signals: list[dict],
                   backtest_summaries: dict[str, dict]) -> CrossValidatorArtifact:
    if probe_art is None:
        signals = [ValidatedSignal(source="llm", code=s.get("code", ""),
                                   direction=s.get("direction", "neutral"),
                                   confidence=float(s.get("confidence", 0.0)),
                                   confidence_label="unverified")
                   for s in llm_signals]
        return CrossValidatorArtifact(signals=signals)

    by_code: dict[str, dict] = {}
    for s in probe_art.signals:
        by_code.setdefault(s.code, {"bullish": 0, "bearish": 0})[s.direction] += 1
    for s in llm_signals:
        code = s.get("code", "")
        by_code.setdefault(code, {"bullish": 0, "bearish": 0})
        by_code[code][s.get("direction", "neutral")] = by_code[code].get(
            s.get("direction", "neutral"), 0) + 1

    confirm, conflict = [], []
    for code, counts in by_code.items():
        b = counts.get("bullish", 0)
        r = counts.get("bearish", 0)
        if b > r:
            confirm.append(code)
        elif r > b:
            conflict.append(code)

    total_confirm = len(confirm)
    total_conflict = len(conflict)
    if total_confirm > total_conflict:
        resolution = "confirmed_via_majority"
    elif total_conflict > total_confirm:
        resolution = "rejected_via_majority"
    else:
        resolution = "tie_pending_review"

    signals = [ValidatedSignal(source="probe", code=s.code, direction=s.direction,
                               confidence=s.confidence, confidence_label="confirmed",
                               timestamp=s.timestamp)
               for s in probe_art.signals]
    return CrossValidatorArtifact(confirm=confirm, conflict=conflict,
                                  resolution=resolution, signals=signals)