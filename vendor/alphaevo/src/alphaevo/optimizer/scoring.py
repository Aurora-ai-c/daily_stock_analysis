"""Shared scoring utilities for strategy optimization candidates."""

from __future__ import annotations

from typing import Literal

from alphaevo.models.execution import EvaluationReport

OptimizationObjective = Literal[
    "confidence",
    "win_rate",
    "avg_return",
    "drawdown",
    "quality",
    "profit_quality",
    "robust_profit_quality",
]


def normalize_objective(objective: str) -> OptimizationObjective:
    """Normalize objective aliases used by CLI and optimizer APIs."""
    key = objective.strip().lower().replace("-", "_")
    aliases = {
        "score": "confidence",
        "confidence_score": "confidence",
        "wr": "win_rate",
        "winrate": "win_rate",
        "return": "avg_return",
        "avg": "avg_return",
        "mdd": "drawdown",
        "max_drawdown": "drawdown",
        "balanced": "quality",
        "edge": "quality",
        "expectancy": "profit_quality",
        "profit": "profit_quality",
        "profitability": "profit_quality",
        "payoff": "profit_quality",
        "return_quality": "profit_quality",
        "robust": "robust_profit_quality",
        "robust_profit": "robust_profit_quality",
        "robust_expectancy": "robust_profit_quality",
        "stable_profit": "robust_profit_quality",
    }
    key = aliases.get(key, key)
    valid = {
        "confidence",
        "win_rate",
        "avg_return",
        "drawdown",
        "quality",
        "profit_quality",
        "robust_profit_quality",
    }
    if key not in valid:
        raise ValueError(f"Unsupported optimization objective: {objective}")
    return key  # type: ignore[return-value]


def objective_value(evaluation: EvaluationReport, objective: OptimizationObjective) -> float:
    """Return the scalar value for one optimization objective."""
    ev = evaluation.overall
    if objective == "win_rate":
        return ev.win_rate
    if objective == "avg_return":
        return ev.avg_return
    if objective == "drawdown":
        return -ev.max_drawdown
    if objective == "quality":
        return quality_score(evaluation)
    if objective == "profit_quality":
        return profit_quality_score(evaluation)
    if objective == "robust_profit_quality":
        return robust_profit_quality_score(evaluation)
    return evaluation.confidence_score


def quality_score(evaluation: EvaluationReport) -> float:
    """Blend win rate and payoff quality into a bounded optimization score.

    The score intentionally gives no credit for negative average return or a
    profit/loss ratio below 1.0. This prevents high-win-rate candidates with
    poor payoff asymmetry from dominating the search.
    """
    ev = evaluation.overall
    win_rate_score = _clamp((ev.win_rate - 0.45) / 0.15)
    avg_return_score = _clamp(ev.avg_return / 0.02)
    profit_loss_score = _clamp((ev.profit_loss_ratio - 1.0) / 1.0)
    drawdown_score = _clamp(1.0 - ev.max_drawdown / 0.40)
    signal_reliability = _clamp(ev.signal_count / 30.0)
    raw_score = (
        0.30 * win_rate_score
        + 0.30 * avg_return_score
        + 0.20 * profit_loss_score
        + 0.20 * drawdown_score
    )
    return round(raw_score * signal_reliability, 6)


def profit_quality_score(evaluation: EvaluationReport) -> float:
    """Return-focused score inspired by hyperopt loss functions.

    Compared with :func:`quality_score`, this gives more weight to average
    return, compounded total return, and payoff ratio while still requiring a
    usable win rate and drawdown profile.
    """
    ev = evaluation.overall
    avg_return_score = _clamp(ev.avg_return / 0.015)
    total_return_score = _clamp(ev.total_return / 0.20)
    profit_loss_score = _clamp((ev.profit_loss_ratio - 1.0) / 1.5)
    win_rate_score = _clamp((ev.win_rate - 0.45) / 0.15)
    drawdown_score = _clamp(1.0 - ev.max_drawdown / 0.35)
    signal_reliability = _clamp(ev.signal_count / 30.0)
    raw_score = (
        0.35 * avg_return_score
        + 0.20 * total_return_score
        + 0.20 * profit_loss_score
        + 0.15 * win_rate_score
        + 0.10 * drawdown_score
    )
    if ev.avg_return <= 0 or ev.profit_loss_ratio < 1.0:
        raw_score *= 0.25
    return round(raw_score * signal_reliability, 6)


def robustness_score(evaluation: EvaluationReport) -> float:
    """Score train/validation/test and walk-forward stability.

    Fast evaluation includes train/validation/test anti-overfit metrics but not
    walk-forward folds, so walk-forward dimensions are neutral until a full
    candidate re-evaluation populates them.
    """
    anti = evaluation.anti_overfit
    train_val_score = _clamp(1.0 - anti.train_val_gap / 0.20)
    val_test_score = _clamp(1.0 - anti.val_test_gap / 0.15)
    if evaluation.walk_forward:
        walk_forward_gap_score = _clamp(1.0 - anti.walk_forward_gap / 0.25)
        walk_forward_pass_score = _clamp(anti.walk_forward_pass_rate)
    else:
        walk_forward_gap_score = 0.65
        walk_forward_pass_score = 0.65
    overfit_score = 0.0 if anti.is_overfit else 1.0
    raw_score = (
        0.30 * train_val_score
        + 0.25 * val_test_score
        + 0.20 * walk_forward_gap_score
        + 0.15 * walk_forward_pass_score
        + 0.10 * overfit_score
    )
    return round(raw_score, 6)


def robust_profit_quality_score(evaluation: EvaluationReport) -> float:
    """Blend return quality with anti-overfit stability."""
    return round(
        0.65 * profit_quality_score(evaluation) + 0.35 * robustness_score(evaluation),
        6,
    )


def candidate_sort_key(
    evaluation: EvaluationReport,
    *,
    passed_gate: bool,
    objective: OptimizationObjective,
) -> tuple[float, float, float, float, float, float, float, float, float, int, float]:
    """Return a stable sort key that keeps payoff quality in tie-breaks."""
    ev = evaluation.overall
    return (
        1.0 if passed_gate else 0.0,
        objective_value(evaluation, objective),
        robustness_score(evaluation),
        profit_quality_score(evaluation),
        quality_score(evaluation),
        evaluation.confidence_score,
        ev.avg_return,
        ev.total_return,
        ev.profit_loss_ratio,
        ev.signal_count,
        -ev.max_drawdown,
    )


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(upper, max(lower, value))
