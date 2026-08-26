# -*- coding: utf-8 -*-
"""alphaevo bridge: dual-track support comparison (judge_support + rendering)."""

from unittest.mock import patch

from src.alphaevo_bridge import (
    _SUPPORT_MARKS,
    judge_support,
    render_strategy_signal_section,
)
from src.analyzer import AnalysisResult

_PAYLOAD = {
    "as_of_date": "2026-08-13",
    "limit_basis": "prev_close",
    "symbols": {
        "600519": {
            "name": "贵州茅台", "st": False, "as_of_date": "2026-08-13",
            "groups": {
                "trend": {"level": "bullish", "vote_ratio": 0.6667, "triggered": 2,
                          "total": 3, "signals": {}},
                "reversal": {"level": "bearish", "vote_ratio": 0.0, "triggered": 0,
                             "total": 2, "signals": {}},
            },
        },
        "300750": {
            "name": "宁德时代", "st": False, "as_of_date": "2026-08-13",
            "groups": {
                "trend": {"level": "neutral", "vote_ratio": 0.3333, "triggered": 1,
                          "total": 3, "signals": {}},
            },
        },
    },
    "data_issues": {},
}


async def _fake_probe(*a, **k):
    return _PAYLOAD


def _render(results):
    with patch("src.strategy_signal_probe.run_probe", side_effect=_fake_probe):
        with patch.dict("os.environ", {"STRATEGY_SIGNALS_ENABLED": "true"}):
            return render_strategy_signal_section(results)


def test_judge_support_llm_bullish():
    assert judge_support(75, "bullish") == "agree"
    assert judge_support(75, "bearish") == "no_signal"
    assert judge_support(75, "neutral") is None


def test_judge_support_llm_bearish():
    assert judge_support(20, "bullish") == "conflict"
    assert judge_support(20, "bearish") is None
    assert judge_support(20, "neutral") is None


def test_judge_support_neutral_or_missing():
    assert judge_support(50, "bullish") is None
    assert judge_support(50, "bearish") is None
    assert judge_support(None, "bullish") is None
    assert judge_support(None, "bearish") is None


def test_judge_support_score_boundaries():
    assert judge_support(61, "bullish") == "agree"
    assert judge_support(60, "bullish") is None
    assert judge_support(39, "bullish") == "conflict"
    assert judge_support(40, "bullish") is None


def test_render_marks_agree_and_no_signal():
    results = [AnalysisResult(code="600519", name="贵州茅台",
                              trend_prediction="看多", operation_advice="买入",
                              sentiment_score=75)]
    text = _render(results)
    assert text is not None
    assert _SUPPORT_MARKS["agree"] in text
    assert _SUPPORT_MARKS["no_signal"] in text
    assert _SUPPORT_MARKS["conflict"] not in text


def test_render_marks_conflict():
    results = [AnalysisResult(code="600519", name="贵州茅台",
                              trend_prediction="看多", operation_advice="买入",
                              sentiment_score=20)]
    text = _render(results)
    assert _SUPPORT_MARKS["conflict"] in text


def test_render_no_mark_without_llm_score():
    text = _render([])
    assert text is not None
    for mark in _SUPPORT_MARKS.values():
        assert mark not in text


def test_render_bearish_label_is_not_bearish():
    text = _render([])
    assert "⚪未触发" in text
    assert "偏空" not in text
    assert "🔴" not in text


def test_render_neutral_group_no_mark_even_with_score():
    results = [AnalysisResult(code="300750", name="宁德时代",
                              trend_prediction="看多", operation_advice="买入",
                              sentiment_score=75)]
    text = _render(results)
    assert "🟡中性" in text
    catl_tail = text.split("宁德时代")[1]
    assert "⚠️" not in catl_tail
    assert "✓" not in catl_tail
