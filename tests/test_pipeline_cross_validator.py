import pytest
from src.services.pipeline.probe import ProbeArtifact, ProbeSignal
from src.services.pipeline.cross_validator import cross_validate, CrossValidatorArtifact


def _probe_art():
    return ProbeArtifact(
        candidates=["600519"],
        signals=[ProbeSignal(signal="ma_cross", code="600519", direction="bullish",
                             confidence=0.8, timestamp="2026-08-16T10:00:00")],
        probe_score=0.3,
    )


class TestCrossValidator:
    def test_majority_confirmed(self):
        art = cross_validate(
            _probe_art(),
            llm_signals=[{"code": "600519", "direction": "bullish", "confidence": 0.9}],
            backtest_summaries={"600519": {"win_rate": 0.6}},
        )
        assert art.resolution == "confirmed_via_majority"

    def test_conflict_tie_pending(self):
        art = cross_validate(
            _probe_art(),
            llm_signals=[{"code": "600519", "direction": "bearish", "confidence": 0.9}],
            backtest_summaries={},
        )
        assert art.resolution == "tie_pending_review"

    def test_unverified_when_probe_missing(self):
        art = cross_validate(None, llm_signals=[{"code": "600519", "direction": "bullish"}],
                             backtest_summaries={})
        assert art.signals[0].confidence_label == "unverified"

    def test_schema_version(self):
        art = cross_validate(_probe_art(), llm_signals=[], backtest_summaries={})
        assert art.schema_version == 1