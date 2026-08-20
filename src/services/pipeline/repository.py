# -*- coding: utf-8 -*-
"""Pipeline 运行/步骤表仓储。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select

from src.services.pipeline.models import PipelineRun, PipelineStep
from src.storage import Base, DatabaseManager


def ensure_pipeline_tables(manager: Optional[DatabaseManager] = None) -> None:
    """确保 pipeline_runs / pipeline_steps 两表在既有库中存在。

    DatabaseManager.create_all 只在构造时执行一次;若构造早于
    src.services.pipeline.models 的 import,两表不会在建表批次内,
    接线时必须显式补建(幂等)。
    """
    db = manager or DatabaseManager.get_instance()
    import src.services.pipeline.models as _models  # noqa: F401  确保模型注册到共享 Base

    Base.metadata.create_all(db._engine)


def _utc_now_iso() -> str:
    """当前 UTC 时间的 ISO 字符串（用于字符串时间戳列）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PipelineRepository:
    """pipeline_runs / pipeline_steps 表的访问层。

    沿用 DatabaseManager 的 session 管理模式；db_path 仅用于测试注入，
    生产路径使用全局 DatabaseManager 单例。
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            DatabaseManager.reset_instance()
            self.db = DatabaseManager(db_url=f"sqlite:///{db_path}")
        else:
            self.db = DatabaseManager.get_instance()

    def create_run(
        self,
        *,
        run_id: str,
        trigger: str,
        mode: str,
        date: str,
        status: str = "pending",
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        error_summary: Optional[str] = None,
    ) -> PipelineRun:
        """创建一次运行；同 run_id 已存在时直接返回现有记录（幂等）。"""
        with self.db.get_session() as session:
            existing = session.execute(
                select(PipelineRun).where(PipelineRun.run_id == run_id).limit(1)
            ).scalar_one_or_none()
            if existing is not None:
                return existing
            run = PipelineRun(
                run_id=run_id,
                trigger=trigger,
                mode=mode,
                date=date,
                status=status,
                started_at=started_at or _utc_now_iso(),
                completed_at=completed_at,
                error_summary=error_summary,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            return run

    def mark_superseded(self, run_id: str, by_run_id: str) -> None:
        """标记 run_id 已被 by_run_id 取代。"""
        with self.db.get_session() as session:
            run = session.execute(
                select(PipelineRun).where(PipelineRun.run_id == run_id).limit(1)
            ).scalar_one_or_none()
            if run is None:
                return
            run.superseded_by = by_run_id
            session.commit()

    def find_active_run(self, *, mode: str, date: str) -> Optional[PipelineRun]:
        """查找指定 mode/date 下未被取代的活跃运行（单锁语义）。"""
        with self.db.get_session() as session:
            return session.execute(
                select(PipelineRun)
                .where(
                    PipelineRun.mode == mode,
                    PipelineRun.date == date,
                    PipelineRun.superseded_by.is_(None),
                )
                .order_by(PipelineRun.id)
                .limit(1)
            ).scalar_one_or_none()

    def latest_run(self, *, mode: str, date: str) -> Optional[PipelineRun]:
        """沿 superseded 链返回指定 mode/date 的最新运行。"""
        with self.db.get_session() as session:
            run = session.execute(
                select(PipelineRun)
                .where(PipelineRun.mode == mode, PipelineRun.date == date)
                .order_by(PipelineRun.id)
                .limit(1)
            ).scalar_one_or_none()
            if run is None:
                return None
            visited: set[str] = set()
            while run.superseded_by and run.superseded_by not in visited:
                visited.add(run.superseded_by)
                nxt = session.execute(
                    select(PipelineRun)
                    .where(PipelineRun.run_id == run.superseded_by)
                    .limit(1)
                ).scalar_one_or_none()
                if nxt is None:
                    break
                run = nxt
            return run

    def get_run(self, run_id: str) -> Optional[PipelineRun]:
        """按 run_id 查询运行记录。"""
        with self.db.get_session() as session:
            return session.execute(
                select(PipelineRun).where(PipelineRun.run_id == run_id).limit(1)
            ).scalar_one_or_none()

    def update_run_status(
        self,
        *,
        run_id: str,
        status: str,
        error_summary: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> Optional[PipelineRun]:
        """更新 run 的状态字段(status 必更新;error_summary/completed_at 仅在传入时写入)。"""
        with self.db.get_session() as session:
            run = session.execute(
                select(PipelineRun).where(PipelineRun.run_id == run_id).limit(1)
            ).scalar_one_or_none()
            if run is None:
                return None
            run.status = status
            if error_summary is not None:
                run.error_summary = error_summary
            if completed_at is not None:
                run.completed_at = completed_at
            session.commit()
            return run

    def superseded_chain_length(self, *, mode: str, date: str) -> int:
        """返回指定 mode/date 下 superseded 链的长度(节点数,沿 superseded_by 追溯,防环)。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(PipelineRun)
                .where(PipelineRun.mode == mode, PipelineRun.date == date)
            ).scalars().all()
            by_id = {r.run_id: r for r in rows}
            length = 0
            for run in rows:
                visited: set[str] = set()
                cur = run
                while cur.superseded_by and cur.superseded_by in by_id \
                        and cur.superseded_by not in visited:
                    visited.add(cur.superseded_by)
                    cur = by_id[cur.superseded_by]
                    length = max(length, len(visited) + 1)
            return length

    def add_step(
        self,
        *,
        run_id: str,
        step: str,
        status: str,
        artifact_path: Optional[str] = None,
        latency_ms: Optional[int] = None,
        error: Optional[str] = None,
        degraded_reasons: str = "",
    ) -> PipelineStep:
        """为一次运行追加步骤记录。"""
        with self.db.get_session() as session:
            step_row = PipelineStep(
                run_id=run_id,
                step=step,
                status=status,
                artifact_path=artifact_path,
                latency_ms=latency_ms,
                error=error,
                degraded_reasons=degraded_reasons,
            )
            session.add(step_row)
            session.commit()
            session.refresh(step_row)
            return step_row

    def update_step_status(
        self,
        *,
        run_id: str,
        step: str,
        status: str,
        artifact_path: Optional[str] = None,
        latency_ms: Optional[int] = None,
        error: Optional[str] = None,
        degraded_reasons: Optional[str] = None,
    ) -> Optional[PipelineStep]:
        """更新指定步骤的状态与诊断字段（未提供的字段保持不变）。"""
        with self.db.get_session() as session:
            step_row = session.execute(
                select(PipelineStep)
                .where(PipelineStep.run_id == run_id, PipelineStep.step == step)
                .limit(1)
            ).scalar_one_or_none()
            if step_row is None:
                return None
            step_row.status = status
            if artifact_path is not None:
                step_row.artifact_path = artifact_path
            if latency_ms is not None:
                step_row.latency_ms = latency_ms
            if error is not None:
                step_row.error = error
            if degraded_reasons is not None:
                step_row.degraded_reasons = degraded_reasons
            session.commit()
            return step_row

    def steps_for(self, run_id: str) -> List[PipelineStep]:
        """按创建顺序返回一次运行的全部步骤。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(PipelineStep)
                .where(PipelineStep.run_id == run_id)
                .order_by(PipelineStep.id)
            ).scalars().all()
            return list(rows)
