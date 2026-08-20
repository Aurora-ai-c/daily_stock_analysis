# -*- coding: utf-8 -*-
"""启动期更新检查线程单元测试。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

from dsa_client.update_thread import start_update_check_thread  # noqa: E402


class TestUpdateThread:
    def test_starts_daemon_thread(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr("dsa_client.update_thread.check_for_update",
                            lambda **kw: calls.append(1) or _result())
        thread = start_update_check_thread(cache_file=str(tmp_path / "c.json"))
        assert isinstance(thread, threading.Thread)
        assert thread.daemon is True
        thread.join(timeout=5)
        assert calls  # 至少跑了一次


def _result():
    from dsa_client.updater import CheckResult
    return CheckResult(update_available=False, current="1.0.0", latest="1.0.0")