# -*- coding: utf-8 -*-
"""步骤 4:报告渲染(无外部副作用,重跑可安全覆盖历史带 seq 文件)。"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class RendererArtifact(BaseModel):
    report_path: str
    format: str
    render_latency: float
    schema_version: Literal[1] = 1


def render_report(artifact_dir: Path, payload: dict) -> RendererArtifact:
    start = time.monotonic()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifact_dir / "report.md"
    report_path.write_text(payload.get("title", "DSA 分析报告") + "\n", encoding="utf-8")
    return RendererArtifact(report_path=str(report_path), format="md",
                            render_latency=round(time.monotonic() - start, 3))