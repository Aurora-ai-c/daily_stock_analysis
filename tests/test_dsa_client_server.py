# -*- coding: utf-8 -*-
"""dsa_client.server 单元测试:TestClient,注入假 GitHub 客户端。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

import dsa_client.config as cfg_mod  # noqa: E402
import dsa_client.server as srv  # noqa: E402


class FakeGit:
    def __init__(self, config):
        self.config = config

    def get_runs(self, owner, repo, limit=5):
        return [{"id": 1, "name": "每日股票分析", "status": "in_progress", "conclusion": None, "run_number": 5}]

    def get_variable(self, owner, repo, name):
        return "600519,600036"

    def set_variable(self, owner, repo, name, value):
        raise AssertionError("不应被调用")

    def dispatch(self, owner, repo, ref="main", inputs=None):
        self._last_inputs = inputs

    def list_artifacts(self, owner, repo, per_page=10):
        return [{"id": 9, "name": "analysis-reports-5", "expired": False}]

    def download_artifact(self, owner, repo, artifact_id):
        return b""  # 空 zip,前端忽略


def _make():
    cfg = cfg_mod.Config()
    cfg.owner = "alice"
    cfg.repo = "dsa-cloud-alice"
    cfg.token = "tok123"
    cfg.pat_enc = "x"
    git = FakeGit(cfg)
    app = srv.create_app(cfg, static_dir=Path("__nonexistent__"), client_factory=lambda config: git)
    return TestClient(app), cfg, git


def test_health_no_token():
    client, _, _ = _make()
    assert client.get("/health").status_code == 200


def test_api_requires_token():
    client, _, _ = _make()
    assert client.get("/api/state").status_code == 403
    assert client.get("/api/state?token=wrong").status_code == 403


def test_state_returns_running_and_watchlist():
    client, _, _ = _make()
    r = client.get("/api/state?token=tok123").json()
    assert r["logged_in"] is True
    assert r["running"] is True  # FakeGit.get_runs 返回 in_progress


def test_watchlist_get():
    client, _, _ = _make()
    assert client.get("/api/watchlist?token=tok123").json()["symbols"] == "600519,600036"


def test_trigger_requires_origin_header():
    client, _, _ = _make()
    assert client.post("/api/trigger?token=tok123", json={"mode": "stocks-only"}).status_code == 403


def test_trigger_ok_with_origin_token():
    client, _, git = _make()
    r = client.post("/api/trigger?token=tok123", headers={"X-Origin-Token": "tok123"},
                    json={"mode": "full", "stock_list": "600519"}).json()
    assert r["ok"] is True
    assert git._last_inputs == {"mode": "full", "stock_list": "600519"}


def test_reports_list():
    client, _, _ = _make()
    reports = client.get("/api/reports?token=tok123").json()["reports"]
    assert reports[0]["name"] == "analysis-reports-5"


def test_login_saves_config():
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as td:
        file = Path(td) / "cfg.json"
        from unittest import mock
        with mock.patch.object(cfg_mod, "config_path", return_value=file):
            client, cfgv, _ = _make()
            r = client.post("/api/login?token=tok123", headers={"X-Origin-Token": "tok123"},
                            json={"owner": "bob", "repo": "dsa-cloud-bob", "pat": "ghp_secret"})
            assert r.status_code == 200
            assert r.json()["ok"] is True
            assert cfgv.get_pat() == "ghp_secret"


def test_login_requires_origin_header():
    client, _, _ = _make()
    assert client.post("/api/login?token=tok123",
                       json={"owner": "bob", "repo": "r", "pat": "p"}).status_code == 403