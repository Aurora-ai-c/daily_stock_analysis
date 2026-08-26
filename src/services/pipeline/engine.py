# -*- coding: utf-8 -*-
"""五步管线编排器:采集→探针→交叉验证→渲染→推送,支持 ReplayMode。

并发语义:同 (mode,date) 已有 active run 且非 force → 直接复用其 run_id;
force → mark_superseded + 新建;superseded 链上限 MAX_SUPERSEDED_CHAIN;
SIDE_EFFECT_FREE 仅对已失败 run_id 开放。
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from src.services.pipeline.collector import collect, CollectorArtifact, CollectorHardFailError
from src.services.pipeline.cross_validator import cross_validate, CrossValidatorArtifact
from src.services.pipeline.probe import probe, ProbeArtifact
from src.services.pipeline.pusher import push_report, PusherArtifact
from src.services.pipeline.renderer import render_report, RendererArtifact
from src.services.run_diagnostics import PipelineStepDiagnostic

SIDE_EFFECT_STEPS = {"pusher"}

MAX_SUPERSEDED_CHAIN = 5


class ReplayMode(Enum):
    FORWARD_ONLY = "forward_only"
    SIDE_EFFECT_FREE = "side_effect_free"
    DRY_RUN = "dry_run"


class PipelineConcurrencyError(RuntimeError):
    """并发/重放语义不满足时的拒绝错误(active 复用、链上限、SIDE_EFFECT_FREE 约束)。"""


class PipelineEngine:
    def __init__(self, repo, runs_dir: Path, manager, channels=None):
        self.repo = repo
        self.runs_dir = Path(runs_dir)
        self.manager = manager
        self.channels = channels or []

    def run(self, mode: str, date: str, stock_codes: list[str],
            replay: ReplayMode = ReplayMode.FORWARD_ONLY,
            force: bool = False, run_id: Optional[str] = None) -> str:
        # 并发去重(用户裁定):非 force 且同 (mode,date) 已有 active run → 只返回其 run_id,
        # 不重跑五步、不追加 step 行、不重复推送(等同旧路径 409 去重)
        if run_id is None and not force:
            active = self.repo.find_active_run(mode=mode, date=date)
            if active is not None:
                return active.run_id
        run_id = self._prepare_run(mode=mode, date=date, replay=replay,
                                   force=force, run_id=run_id)
        artifact_dir = self.runs_dir / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        seq = 0

        # 步骤 1:collector —— hard-fail(任何异常 → run failed,无数据无法继续)
        try:
            start = time.monotonic()
            collector_art = collect(stock_codes, markets=["cn", "us"], manager=self.manager)
        except CollectorHardFailError as exc:
            self._finish(run_id, status="failed", error_summary=str(exc)[:300],
                         completed_at=datetime.now().isoformat())
            return run_id
        except Exception as exc:  # noqa: BLE001
            self._finish(run_id, status="failed", error_summary=str(exc)[:300],
                         completed_at=datetime.now().isoformat())
            return run_id
        seq = self._persist(artifact_dir, "collector", collector_art, seq, run_id,
                            latency_ms=int((time.monotonic() - start) * 1000))

        # 步骤 2:probe —— soft-fail(异常记录 degraded 后继续);真实消费 collector 的 bars
        probe_art: Optional[ProbeArtifact] = None
        try:
            start = time.monotonic()
            bars_by_code = collector_art.bars_by_code or {}
            codes_with_bars = list(bars_by_code) or list(stock_codes)
            probe_art = probe(codes_with_bars, bars_by_code, {})
        except Exception as exc:  # noqa: BLE001
            self._degraded(run_id, "probe", exc,
                           latency_ms=int((time.monotonic() - start) * 1000))
            seq += 1
        else:
            seq = self._persist(artifact_dir, "probe", probe_art, seq, run_id,
                                latency_ms=int((time.monotonic() - start) * 1000))

        # 步骤 3:cross_validator —— soft-fail
        validated: Optional[CrossValidatorArtifact] = None
        try:
            start = time.monotonic()
            validated = cross_validate(probe_art, llm_signals=[], backtest_summaries={})
        except Exception as exc:  # noqa: BLE001
            self._degraded(run_id, "cross_validator", exc,
                           latency_ms=int((time.monotonic() - start) * 1000))
            seq += 1
        else:
            seq = self._persist(artifact_dir, "cross_validator", validated, seq, run_id,
                                latency_ms=int((time.monotonic() - start) * 1000))

        # 步骤 4:renderer —— soft-fail;结构化 payload(采集/探针/验证 → markdown 章节)
        rendered: Optional[RendererArtifact] = None
        try:
            start = time.monotonic()
            payload: dict = {
                "title": f"DSA 管线报告 {mode} {date}",
                "mode": mode,
                "date": date,
                "stocks": list(stock_codes),
                "collector": {
                    "rows": collector_art.rows,
                    "missing_markets": collector_art.missing_markets,
                    "fetchers_used": collector_art.fetchers_used,
                    "latency": collector_art.latency,
                    "codes_with_bars": sorted(collector_art.bars_by_code),
                },
            }
            if probe_art is not None:
                payload["probe"] = {
                    "candidates": probe_art.candidates,
                    "probe_score": probe_art.probe_score,
                    "signals": [s.model_dump() for s in probe_art.signals],
                }
            if validated is not None:
                payload["validated"] = {
                    "confirm": validated.confirm,
                    "conflict": validated.conflict,
                    "resolution": validated.resolution,
                    "signal_count": len(validated.signals),
                }
            rendered = render_report(artifact_dir, payload)
        except Exception as exc:  # noqa: BLE001
            self._degraded(run_id, "renderer", exc,
                           latency_ms=int((time.monotonic() - start) * 1000))
            seq += 1
        else:
            seq = self._persist(artifact_dir, "renderer", rendered, seq, run_id,
                                latency_ms=int((time.monotonic() - start) * 1000))

        # 步骤 5:pusher —— side-effect 门控 + soft-fail + failures 映射进 degraded_reasons
        if replay == ReplayMode.FORWARD_ONLY and self.channels:
            if rendered is None:
                self._degraded(run_id, "pusher", RuntimeError("renderer 失败,无报告可推送"),
                               reason="renderer_failed:no_report_to_push")
                seq += 1
            else:
                try:
                    start = time.monotonic()
                    pushed = push_report(rendered, self.channels)
                except Exception as exc:  # noqa: BLE001
                    self._degraded(run_id, "pusher", exc,
                                   latency_ms=int((time.monotonic() - start) * 1000))
                    seq += 1
                else:
                    seq = self._persist(artifact_dir, "pusher", pushed, seq, run_id,
                                        latency_ms=int((time.monotonic() - start) * 1000))
                    if pushed.failures:
                        self.repo.update_step_status(
                            run_id=run_id, step="pusher", status="degraded",
                            degraded_reasons=json.dumps(pushed.failures, ensure_ascii=False),
                        )
        else:
            self._skip_side_effect(run_id, "pusher", replay)

        self._finish(run_id, status="completed",
                     completed_at=datetime.now().isoformat())
        return run_id

    def _prepare_run(self, *, mode: str, date: str, replay: ReplayMode,
                     force: bool, run_id: Optional[str]) -> str:
        """确定本次执行的 run_id 并初始化 run 记录(重放语义 + force 新建)。

        非 force 复用已在 run() 入口提前返回,此处仅处理 run_id 重放与新建路径。
        """
        if run_id is not None:
            existing = self.repo.get_run(run_id)
            if existing is None:
                raise PipelineConcurrencyError(f"run_id {run_id} not found; cannot replay")
            if replay == ReplayMode.SIDE_EFFECT_FREE \
                    and getattr(existing, "status", None) != "failed":
                raise PipelineConcurrencyError(
                    f"SIDE_EFFECT_FREE 仅对已失败 run 开放;run_id={run_id} "
                    f"status={getattr(existing, 'status', None)}"
                )
            self._finish(run_id, status="running")
            return run_id

        active = self.repo.find_active_run(mode=mode, date=date)
        new_run_id = uuid.uuid4().hex
        if active is not None and force:
            chain_len = self.repo.superseded_chain_length(mode=mode, date=date)
            if chain_len >= MAX_SUPERSEDED_CHAIN:
                raise PipelineConcurrencyError(
                    f"superseded 链已达上限 {MAX_SUPERSEDED_CHAIN} "
                    f"(mode={mode} date={date});拒绝 force 新建"
                )
            self.repo.mark_superseded(active.run_id, by_run_id=new_run_id)
        self.repo.create_run(
            run_id=new_run_id,
            trigger="manual" if force else "cron",
            mode=mode,
            date=date,
            status="running",
            started_at=datetime.now().isoformat(),
        )
        return new_run_id

    def _persist(self, artifact_dir: Path, name: str, artifact, seq: int,
                 run_id: str, *, latency_ms: Optional[int] = None) -> int:
        seq += 1
        payload = artifact.model_dump()
        payload["schema_version"] = 1
        path = artifact_dir / f"step_{seq}_{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        self._record_step(run_id=run_id, step_name=name, status="ok",
                          artifact_path=str(path), latency_ms=latency_ms)
        return seq

    def _degraded(self, run_id: str, step: str, exc: Exception, *,
                  reason: Optional[str] = None,
                  latency_ms: Optional[int] = None) -> None:
        message = reason or str(exc) or exc.__class__.__name__
        self._record_step(
            run_id=run_id, step_name=step, status="degraded",
            latency_ms=latency_ms,
            error=str(exc)[:300] if str(exc) else None,
            degraded_reasons=[message[:300]],
        )

    def _skip_side_effect(self, run_id: str, step: str, replay: ReplayMode):
        self._record_step(run_id=run_id, step_name=step, status="skipped")

    def _record_step(self, *, run_id: str, step_name: str, status: str,
                     latency_ms: Optional[int] = None,
                     artifact_path: Optional[str] = None,
                     error: Optional[str] = None,
                     degraded_reasons: Optional[list[str]] = None) -> None:
        """按 PipelineStepDiagnostic 契约构造诊断并经 sanitize() 落库(等价字段映射)。

        字段映射:step_name→step、error_sanitized→error、degraded_reasons(list)→JSON 字符串。
        """
        diag = PipelineStepDiagnostic(
            run_id=run_id,
            step_name=step_name,
            status=status,
            latency_ms=latency_ms,
            artifact_path=artifact_path,
            error_sanitized=error,
            degraded_reasons=degraded_reasons or [],
        ).sanitize()
        self.repo.add_step(
            run_id=run_id,
            step=step_name,
            status=status,
            artifact_path=diag.get("artifact_path"),
            latency_ms=diag.get("latency_ms"),
            error=diag.get("error_sanitized"),
            degraded_reasons=json.dumps(diag.get("degraded_reasons") or [],
                                        ensure_ascii=False),
        )

    def _finish(self, run_id: str, *, status: str, error_summary: Optional[str] = None,
                completed_at: Optional[str] = None) -> None:
        """run 状态持久化:优先 update_run_status;旧测试替身无该方法时退回就地更新。"""
        update = getattr(self.repo, "update_run_status", None)
        if callable(update):
            update(run_id=run_id, status=status, error_summary=error_summary,
                   completed_at=completed_at)
            return
        self._update_run_record(run_id, status=status, error_summary=error_summary)

    def _update_run_record(self, run_id: str, **fields) -> None:
        """就地更新 run 记录;仅当 repo 暴露 runs dict(旧测试替身)时生效。"""
        runs = getattr(self.repo, "runs", None)
        if isinstance(runs, dict) and run_id in runs:
            runs[run_id].update(fields)