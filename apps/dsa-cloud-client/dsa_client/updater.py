# -*- coding: utf-8 -*-
"""客户端更新:检查(缓存)→ 下载校验 → updater.exe 原子替换。"""
from __future__ import annotations

import hashlib
import json
import platform as _platform
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from packaging.version import Version
from pydantic import BaseModel

from dsa_client.version import get_version

# 控制器裁定:更新源使用用户 fork Aurora-ai-c(非计划原文 ZhuLinsen)
UPDATE_JSON_URL = ("https://github.com/Aurora-ai-c/daily_stock_analysis/"
                   "releases/latest/download/updates.json")
CACHE_TTL_SECONDS = 24 * 3600


class CheckResult(BaseModel):
    """更新检查结果。"""

    update_available: bool
    current: str
    latest: str
    url: Optional[str] = None
    sha256: Optional[str] = None
    notes: Optional[str] = None
    cached: bool = False
    error: Optional[str] = None


def fetch_updates_json(timeout: int = 15) -> dict:
    with urllib.request.urlopen(UPDATE_JSON_URL, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def select_update(updates: list[dict], platform: str, arch: str,
                  channel: str, current: str) -> Optional[dict]:
    candidates = [
        u for u in updates
        if u.get("platform") == platform and u.get("arch") == arch
        and u.get("channel") == channel
    ]
    cur = Version(current)
    best = None
    for u in candidates:
        v = Version(u["version"])
        if v.is_prerelease or v.is_devrelease or v.local:
            continue
        if v <= cur:
            continue
        if best is None or v > Version(best["version"]):
            best = u
    return best


def _platform_key() -> tuple[str, str]:
    sys_platform = _platform.system().lower()
    plat = "win" if sys_platform == "windows" else sys_platform
    arch = "x64" if _platform.machine().lower() in ("amd64", "x86_64") else "arm64"
    return plat, arch


def check_for_update(cache_file: Optional[str] = None) -> "CheckResult":
    cache_path = Path(cache_file) if cache_file else Path("data/update_check_cache.json")
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            age = time.time() - datetime.fromisoformat(
                cached["checked_at"].replace("Z", "+00:00")).timestamp()
            if age < CACHE_TTL_SECONDS:
                return CheckResult(update_available=True, current=get_version(),
                                   latest=cached["latest"], url=cached["url"],
                                   sha256=cached["sha256"], notes=cached.get("notes"),
                                   cached=True)
        except (KeyError, ValueError, OSError):
            pass
    try:
        payload = fetch_updates_json()
        plat, arch = _platform_key()
        chosen = select_update(payload.get("updates", []), plat, arch,
                               "stable", get_version())
        now = datetime.now(timezone.utc).isoformat()
        if chosen:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"checked_at": now, **chosen}),
                                  encoding="utf-8")
            return CheckResult(update_available=True, current=get_version(),
                               latest=chosen["version"], url=chosen["url"],
                               sha256=chosen["sha256"], notes=chosen.get("notes"),
                               cached=False)
        return CheckResult(update_available=False, current=get_version(),
                           latest=get_version(), cached=False)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(update_available=False, current=get_version(),
                           latest=get_version(), error=str(exc)[:200], cached=False)