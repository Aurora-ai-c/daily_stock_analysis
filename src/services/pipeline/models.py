# -*- coding: utf-8 -*-
"""Pipeline 运行/步骤表模型。"""
from __future__ import annotations

from sqlalchemy import Column, Integer, String, Text

from src.storage import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), unique=True, nullable=False, index=True)
    trigger = Column(String(32), nullable=False)
    mode = Column(String(32), nullable=False)
    date = Column(String(16), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending")
    started_at = Column(String(32), nullable=False)
    completed_at = Column(String(32), nullable=True)
    error_summary = Column(Text, nullable=True)
    superseded_by = Column(String(64), nullable=True)


class PipelineStep(Base):
    __tablename__ = "pipeline_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, index=True)
    step = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False)
    artifact_path = Column(String(255), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    degraded_reasons = Column(Text, nullable=True)
