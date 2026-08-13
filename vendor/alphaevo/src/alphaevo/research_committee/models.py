"""Pydantic models for deterministic research committee output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from alphaevo.models.execution import StrategyChange

VerdictStatus = Literal["pass", "watch", "fail"]


class AnalystVerdict(BaseModel):
    """One analyst's structured assessment."""

    analyst: str
    status: VerdictStatus = "watch"
    summary: str
    evidence: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class CommitteeVerdict(BaseModel):
    """Aggregated deterministic research committee output."""

    strategy_id: str
    overall_status: VerdictStatus = "watch"
    thesis: str = ""
    verdicts: list[AnalystVerdict] = Field(default_factory=list)
    mutation_plan: list[StrategyChange] = Field(default_factory=list)

    @property
    def failed_count(self) -> int:
        """Number of analyst verdicts marked as fail."""
        return sum(1 for verdict in self.verdicts if verdict.status == "fail")

    @property
    def watch_count(self) -> int:
        """Number of analyst verdicts marked as watch."""
        return sum(1 for verdict in self.verdicts if verdict.status == "watch")
