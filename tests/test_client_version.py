# -*- coding: utf-8 -*-
"""dsa_client.version 单源版本读取单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

from dsa_client.version import VERSION_FILE, get_version  # noqa: E402


def test_version_file_format(tmp_path):
    f = tmp_path / VERSION_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    content = f.read_text(encoding="utf-8").strip()
    assert content == '__version__ = "1.2.3"'
    assert "\n" not in content.split('"')[1]


class TestGetVersion:
    def test_reads_version_file(self, monkeypatch, tmp_path):
        f = tmp_path / "_version.py"
        f.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
        monkeypatch.setattr("dsa_client.version.VERSION_MODULE_PATH", str(f))
        assert get_version() == "1.2.3"

    def test_fallback_dev(self, monkeypatch, tmp_path):
        monkeypatch.setattr("dsa_client.version.VERSION_MODULE_PATH", str(tmp_path / "missing.py"))
        assert get_version() == "0.0.0-dev"
