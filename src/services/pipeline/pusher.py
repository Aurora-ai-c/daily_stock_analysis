# -*- coding: utf-8 -*-
"""步骤 5:推送(side_effects=True,重跑跳过;指数退避 1s/4s/16s)。"""
from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel

from src.services.pipeline.renderer import RendererArtifact

BACKOFF_SECONDS = (1.0, 4.0, 16.0)


class PusherArtifact(BaseModel):
    channels: list[str] = []
    per_channel_status: dict[str, str] = {}
    failures: list[str] = []
    schema_version: Literal[1] = 1


def push_report(rendered: RendererArtifact, channels: list) -> PusherArtifact:
    per_channel: dict[str, str] = {}
    failures: list[str] = []
    for channel in channels:
        name = getattr(channel, "name", channel.__class__.__name__)
        try:
            for attempt in range(3):
                try:
                    channel.send({"report_path": rendered.report_path})
                    per_channel[name] = "ok"
                    break
                except Exception:  # noqa: BLE001
                    if attempt == 2:
                        raise
                    time.sleep(BACKOFF_SECONDS[attempt])
        except Exception as exc:  # noqa: BLE001
            per_channel[name] = "failed"
            failures.append(f"{name}:{exc}")
    return PusherArtifact(channels=[getattr(c, "name", c.__class__.__name__) for c in channels],
                          per_channel_status=per_channel, failures=failures)