# -*- coding: utf-8 -*-
"""步骤 2:信号探针。v1 最小集 6 个确定性技术信号,source="probe"。"""
from __future__ import annotations

import statistics
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

from data_provider.contracts import Bar, Quote

SIGNAL_WEIGHTS = {
    "ma_cross": 0.3, "volume_surge": 0.2, "breakout": 0.2,
    "pct_movement": 0.1, "fund_flow": 0.1, "volume_price_divergence": 0.1,
}


class ProbeSignal(BaseModel):
    signal: str
    code: str
    direction: Literal["bullish", "bearish"]
    confidence: float
    source: Literal["probe"] = "probe"
    timestamp: str = ""


class ProbeArtifact(BaseModel):
    candidates: list[str] = []
    signals: list[ProbeSignal] = []
    probe_score: float = 0.0
    schema_version: Literal[1] = 1


def probe(codes: list[str], bars_by_code: dict[str, list[Bar]],
          quote_by_code: dict[str, Quote]) -> ProbeArtifact:
    signals: list[ProbeSignal] = []
    for code in codes:
        bars = bars_by_code.get(code) or []
        signals.extend(_ma_cross(code, bars))
        signals.extend(_volume_surge(code, bars))
        signals.extend(_breakout(code, bars))
        signals.extend(_pct_movement(code, bars))
        signals.extend(_volume_price_divergence(code, bars))
    for s in signals:
        s.timestamp = datetime.now().isoformat()
    candidates = sorted({s.code for s in signals})
    score = _score(signals)
    return ProbeArtifact(candidates=candidates, signals=signals, probe_score=score)


def _score(signals: list[ProbeSignal]) -> float:
    if not signals:
        return 0.0
    num = sum(s.confidence * SIGNAL_WEIGHTS.get(s.signal, 0.1) for s in signals)
    den = sum(SIGNAL_WEIGHTS.get(s.signal, 0.1) for s in signals)
    return round(min(1.0, num / den), 3) if den else 0.0


def _ma_cross(code: str, bars: list[Bar]) -> list[ProbeSignal]:
    if len(bars) < 21:
        return []
    closes = [b.close for b in bars]
    ma5_prev = statistics.mean(closes[-6:-1])
    ma20_prev = statistics.mean(closes[-21:-1])
    ma5_cur = statistics.mean(closes[-5:])
    ma20_cur = statistics.mean(closes[-20:])
    if ma5_prev <= ma20_prev and ma5_cur > ma20_cur:
        return [ProbeSignal(signal="ma_cross", code=code, direction="bullish", confidence=0.8)]
    if ma5_prev >= ma20_prev and ma5_cur < ma20_cur:
        return [ProbeSignal(signal="ma_cross", code=code, direction="bearish", confidence=0.8)]
    return []


def _volume_surge(code: str, bars: list[Bar]) -> list[ProbeSignal]:
    if len(bars) < 6:
        return []
    cur = bars[-1].volume
    prev_5 = [b.volume for b in bars[-6:-1]]
    avg = statistics.mean(prev_5) if prev_5 else 0
    if avg <= 0:
        return []
    ratio = cur / avg
    if ratio > 2.0:
        return [ProbeSignal(signal="volume_surge", code=code, direction="bullish", confidence=min(1.0, ratio / 4))]
    if ratio < 0.5:
        return [ProbeSignal(signal="volume_surge", code=code, direction="bearish", confidence=0.6)]
    return []


def _breakout(code: str, bars: list[Bar]) -> list[ProbeSignal]:
    if len(bars) < 21:
        return []
    window = [b.close for b in bars[:-1]][-20:]
    if not window:
        return []
    hi20 = max(window)
    lo20 = min(window)
    cur = bars[-1].close
    if cur > hi20:
        return [ProbeSignal(signal="breakout", code=code, direction="bullish", confidence=0.7)]
    if cur < lo20:
        return [ProbeSignal(signal="breakout", code=code, direction="bearish", confidence=0.7)]
    return []


def _pct_movement(code: str, bars: list[Bar]) -> list[ProbeSignal]:
    if len(bars) < 21:
        return []
    pcts = [b.pct_chg for b in bars[-20:] if b.pct_chg is not None]
    if len(pcts) < 20:
        return []
    cur = bars[-1].pct_chg
    if cur is None:
        return []
    mean = statistics.mean(pcts)
    stdev = statistics.pstdev(pcts) or 1.0
    z = (cur - mean) / stdev
    if abs(z) > 2:
        direction = "bullish" if z > 0 else "bearish"
        return [ProbeSignal(signal="pct_movement", code=code, direction=direction, confidence=min(1.0, abs(z) / 3))]
    return []


def _volume_price_divergence(code: str, bars: list[Bar]) -> list[ProbeSignal]:
    if len(bars) < 4:
        return []
    last3 = bars[-3:]
    prices_up = last3[-1].close > last3[0].close
    vols_up = last3[-1].volume > last3[0].volume
    if prices_up and not vols_up:
        return [ProbeSignal(signal="volume_price_divergence", code=code, direction="bearish", confidence=0.5)]
    if not prices_up and vols_up:
        return [ProbeSignal(signal="volume_price_divergence", code=code, direction="bullish", confidence=0.5)]
    return []
