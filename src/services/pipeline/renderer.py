# -*- coding: utf-8 -*-
"""步骤 4:报告渲染(无外部副作用,重跑可安全覆盖历史带 seq 文件)。

v2 接线:结构化 payload(collector/probe/validated)→ markdown 章节。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class RendererArtifact(BaseModel):
    report_path: str
    format: str
    render_latency: float
    schema_version: Literal[1] = 1


_SECTION_TITLES = {
    "collector": "数据采集",
    "probe": "信号探针",
    "validated": "交叉验证",
}


def render_report(artifact_dir: Path, payload: dict) -> RendererArtifact:
    start = time.monotonic()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifact_dir / "report.md"
    lines: list[str] = [str(payload.get("title", "DSA 分析报告")), ""]
    meta = {k: v for k, v in payload.items()
            if k in ("mode", "date", "stocks") and v is not None}
    if meta:
        lines += ["```json", json.dumps(meta, ensure_ascii=False, default=str), "```", ""]
    for key in ("collector", "probe", "validated"):
        if key not in payload:
            continue
        lines += [f"## {_SECTION_TITLES[key]} ({key})", "",
                  "```json",
                  json.dumps(payload[key], ensure_ascii=False, indent=2, default=str),
                  "```", ""]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return RendererArtifact(report_path=str(report_path), format="md",
                            render_latency=round(time.monotonic() - start, 3))
