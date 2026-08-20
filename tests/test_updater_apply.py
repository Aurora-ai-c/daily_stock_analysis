# -*- coding: utf-8 -*-
"""dsa_client.updater_apply 更新应用纯函数测试:sha256 校验、备份 LRU。"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

from dsa_client.updater_apply import verify_sha256, plan_backup  # noqa: E402


def test_verify_sha256_ok(tmp_path):
    f = tmp_path / "a.bin"
    data = b"hello"
    f.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    assert verify_sha256(str(f), digest) is True


def test_verify_sha256_mismatch(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    assert verify_sha256(str(f), hashlib.sha256(b"world").hexdigest()) is False


def test_plan_backup_keeps_latest_three(tmp_path):
    for v in ["0.1.0", "0.2.0", "0.3.0", "0.4.0"]:
        (tmp_path / f"{v}_app.exe.bak").write_bytes(b"x")
    to_delete = plan_backup(str(tmp_path), keep=3)
    assert len(to_delete) == 1
    assert "0.1.0" in to_delete[0]