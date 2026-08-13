"""Deterministic research committee orchestration."""

from __future__ import annotations

from alphaevo.models.execution import EvaluationReport, StrategyChange
from alphaevo.models.strategy import Strategy
from alphaevo.research_committee.analysts import (
    data_quality_verdict,
    mutation_planner_verdict,
    overfit_verdict,
    risk_verdict,
    technical_verdict,
)
from alphaevo.research_committee.models import CommitteeVerdict


class ResearchCommittee:
    """Run deterministic analyst checks over one strategy evaluation."""

    def review(
        self,
        strategy: Strategy,
        report: EvaluationReport,
        *,
        data_source: str = "unknown",
        symbols: list[str] | None = None,
        mutation_plan: list[StrategyChange] | None = None,
    ) -> CommitteeVerdict:
        """Return structured analyst verdicts for a strategy evaluation."""
        planned = mutation_plan or []
        symbol_list = symbols or []
        verdicts = [
            technical_verdict(strategy, report),
            risk_verdict(report),
            overfit_verdict(report),
            data_quality_verdict(report, data_source=data_source, symbols=symbol_list),
            mutation_planner_verdict(report, planned),
        ]

        if any(verdict.status == "fail" for verdict in verdicts):
            overall = "fail"
        elif any(verdict.status == "watch" for verdict in verdicts):
            overall = "watch"
        else:
            overall = "pass"

        thesis = (
            "Promote only evidence-backed mutations; reject prettier output if the retest "
            "does not improve the measured strategy."
        )

        return CommitteeVerdict(
            strategy_id=strategy.meta.id,
            overall_status=overall,  # type: ignore[arg-type]
            thesis=thesis,
            verdicts=verdicts,
            mutation_plan=planned,
        )
