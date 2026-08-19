# tests/test_pipeline_probe.py
import pytest
from datetime import date, timedelta
from data_provider.contracts import Bar, Quote
from src.services.pipeline.probe import probe, ProbeArtifact, ProbeSignal


def _bars(closes, vols):
    out = []
    start = date(2026, 7, 1)
    for i, (c, v) in enumerate(zip(closes, vols)):
        out.append(Bar(date=start + timedelta(days=i), open=c, high=c * 1.01,
                       low=c * 0.99, close=c, volume=v))
    return out


class TestProbe:
    def test_ma_cross_detected(self):
        # 前 29 日横盘,末交易日突跳上穿,MA5 上穿 MA20
        closes = [10.0] * 29 + [11.0]  # 突跳
        bars = _bars(closes, [1000] * 30)
        art = probe(["600519"], {"600519": bars}, {})
        signals = [s for s in art.signals if s.signal == "ma_cross"]
        assert signals and signals[0].direction == "bullish"

    def test_score_in_range(self):
        closes = [10] * 30
        bars = _bars(closes, [1000] * 30)
        art = probe(["600519"], {"600519": bars}, {})
        assert 0.0 <= art.probe_score <= 1.0

    def test_no_signals_empty_candidates(self):
        art = probe([], {}, {})
        assert art.candidates == []
        assert art.probe_score == 0.0
