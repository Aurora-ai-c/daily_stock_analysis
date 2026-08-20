# -*- coding: utf-8 -*-
"""dsa_client.updater 更新检查(缓存/版本比较)单元测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

from dsa_client.updater import select_update, CheckResult  # noqa: E402


UPDATES = [
    {"version": "0.9.0", "platform": "win", "arch": "x64", "channel": "stable",
     "url": "u1", "sha256": "a"},
    {"version": "1.0.0", "platform": "win", "arch": "x64", "channel": "stable",
     "url": "u2", "sha256": "b"},
    {"version": "1.1.0-rc1", "platform": "win", "arch": "x64", "channel": "stable",
     "url": "u3", "sha256": "c"},  # prerelease 应被忽略
    {"version": "1.2.0", "platform": "win", "arch": "arm64", "channel": "stable",
     "url": "u4", "sha256": "d"},  # arch 不匹配
]


class TestSelectUpdate:
    def test_picks_highest_stable_matching(self):
        got = select_update(UPDATES, "win", "x64", "stable", "0.5.0")
        assert got is not None and got["version"] == "1.0.0"

    def test_none_when_current_newer(self):
        assert select_update(UPDATES, "win", "x64", "stable", "2.0.0") is None

    def test_ignores_prerelease(self):
        got = select_update(UPDATES, "win", "x64", "stable", "1.0.0")
        assert got is not None and got["version"] == "1.0.0" or got is None
        got2 = select_update(UPDATES, "win", "x64", "stable", "0.9.9")
        assert got2["version"] == "1.0.0"


class TestCache:
    def test_cache_hit_short_circuits(self, tmp_path):
        cache = tmp_path / "c.json"
        cache.write_text(json.dumps({"checked_at": "2999-01-01T00:00:00Z",
                                     "latest": "1.0.0", "url": "u", "sha256": "s"}),
                         encoding="utf-8")
        from dsa_client.updater import check_for_update
        result = check_for_update(cache_file=str(cache))
        assert result.cached is True and result.latest == "1.0.0"

    def test_stale_cache_refetches(self, tmp_path):
        cache = tmp_path / "c.json"
        cache.write_text(json.dumps({"checked_at": "2000-01-01T00:00:00Z",
                                     "latest": "0.1.0", "url": "u", "sha256": "s"}),
                         encoding="utf-8")
        from dsa_client.updater import check_for_update
        result = check_for_update(cache_file=str(cache))
        assert result.cached is False or result.error is not None  # 离线环境容忍 error