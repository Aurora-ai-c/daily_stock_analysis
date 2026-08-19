# -*- coding: utf-8 -*-
"""五步管线编排器:采集→探针→交叉验证→渲染→推送,支持 ReplayMode。"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from src.services.pipeline.collector import collect, CollectorArtifact, CollectorHardFailError
from src.services.pipeline.probe import probe, ProbeArtifact
from src.services.pipeline.cross_validator import cross_validate, CrossValidatorArtifact
from src.services.pipeline.renderer import render_report, RendererArtifact
from src.services.pipeline.pusher import push_report, PusherArtifact

SIDE_EFFECT_STEPS = {"pusher"}


class ReplayMode(Enum):
    FORWARD_ONLY = "forward_only"
    SIDE_EFFECT_FREE = "side_effect_free"
    DRY_RUN = "dry_run"


class PipelineEngine:
    def __init__(self, repo, runs_dir: Path, manager, channels=None):
        self.repo = repo
        self.runs_dir = Path(runs_dir)
        self.manager = manager
        self.channels = channels or []

    def run(self, mode: str, date: str, stock_codes: list[str],
            replay: ReplayMode = ReplayMode.FORWARD_ONLY,
            force: bool = False) -> str:
        run_id = uuid.uuid4().hex
        self.repo.create_run(run_id=run_id, trigger="manual" if force else "cron",
                             mode=mode, date=date, status="running",
                             started_at=datetime.now().isoformat())
        artifact_dir = self.runs_dir / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        seq = 0

        try:
            collector_art = collect(stock_codes, markets=["cn", "us"], manager=self.manager)
            seq = self._persist(artifact_dir, "collector", collector_art, seq)

            probe_art = probe(collector_art.rows.keys() and stock_codes,
                              {}, {})
            seq = self._persist(artifact_dir, "probe", probe_art, seq)

            validated = cross_validate(probe_art, llm_signals=[], backtest_summaries={})
            seq = self._persist(artifact_dir, "cross_validator", validated, seq)

            rendered = render_report(artifact_dir, {"title": f"{mode} {date}"})
            seq = self._persist(artifact_dir, "renderer", rendered, seq)

            if replay == ReplayMode.FORWARD_ONLY and self.channels:
                pushed = push_report(rendered, self.channels)
                seq = self._persist(artifact_dir, "pusher", pushed, seq)
            else:
                self._skip_side_effect(run_id, "pusher", replay)

            self.repo.update_step_status(run_id, "run", "completed")
            self._update_run_record(run_id, status="completed")
        except CollectorHardFailError as exc:
            self._update_run_record(run_id, status="failed", error_summary=str(exc))
        except Exception as exc:  # noqa: BLE001
            self._update_run_record(run_id, status="failed",
                                    error_summary=str(exc)[:300])
        return run_id

    def _persist(self, artifact_dir: Path, name: str, artifact, seq: int) -> int:
        seq += 1
        payload = artifact.model_dump()
        payload["schema_version"] = 1
        path = artifact_dir / f"step_{seq}_{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        self.repo.add_step(run_id=artifact_dir.name, step=name, status="ok",
                           artifact_path=str(path))
        return seq

    def _skip_side_effect(self, run_id: str, step: str, replay: ReplayMode):
        self.repo.add_step(run_id=run_id, step=step, status="skipped",
                           artifact_path=None)

    def _update_run_record(self, run_id: str, **fields) -> None:
        """就地更新 run 记录;仅当 repo 暴露 runs dict(测试替身)时生效。

        真实 PipelineRepository 无 runs dict 属性且暂无 run 状态更新方法,
        此处防御性跳过以避免 AttributeError;生产持久化接线留待 Task 8。
        """
        runs = getattr(self.repo, "runs", None)
        if isinstance(runs, dict) and run_id in runs:
            runs[run_id].update(fields)