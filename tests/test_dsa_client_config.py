# -*- coding: utf-8 -*-
"""dsa_client.config 单元测试。DPAPI 加解密仅在 Windows 下真实往返。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

import dsa_client.config as cfg  # noqa: E402


def test_generate_token_length_and_charset(monkeypatch):
    token = cfg.generate_token()
    assert len(token) >= 40  # 32 bytes urlsafe b64 without padding
    assert token == token.replace("+", "").replace("/", "")


def test_config_get_set_pat_roundtrip_windows(monkeypatch):
    if not sys.platform.startswith("win"):
        pytest.skip("DPAPI 仅 Windows")
    file = Path(cfg.CONFIG_DIR) / f"test_cfg_{id(Path)}.json"
    monkeypatch.setattr(cfg, "config_path", lambda: file)
    c = cfg.Config()
    c.owner = "alice"
    c.repo = "dsa-cloud-alice"
    c.set_pat("ghp_secret")
    c.save()
    c2 = cfg.Config.load()
    assert c2.owner == "alice"
    assert c2.repo == "dsa-cloud-alice"
    assert c2.get_pat() == "ghp_secret"
    assert c2.pat_enc != "ghp_secret"  # 不能明文
    file.unlink(missing_ok=True)


def test_initialize_makes_new_token():
    file = Path(cfg.CONFIG_DIR) / f"test_cfg_init_{id(Path)}.json"
    from unittest import mock
    with mock.patch.object(cfg, "config_path", return_value=file):
        file.unlink(missing_ok=True)
        c = cfg.initialize_config()
        assert c.token
        assert file.exists()
        c2 = cfg.initialize_config()
        assert c2.token == c.token  # 已存在则复用
    file.unlink(missing_ok=True)


def test_validate_reports_missing():
    c = cfg.Config()
    missing = c.validate()
    assert "owner" in missing and "repo" in missing
    c.owner = "alice"
    assert "owner" not in c.validate()
