# -*- coding: utf-8 -*-
"""三项升级的单元测试:网络逃生 / 可观测性 / 成本护栏。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

import dsa_client.config as cfg_mod  # noqa: E402
import dsa_client.server as srv  # noqa: E402
import dsa_client.state_store as ss  # noqa: E402


class FakeGit:
    def __init__(self, config, runs=None):
        self.config = config
        self._runs = runs or [{"id": 1, "name": "每日股票分析", "status": "in_progress",
                               "conclusion": None, "run_number": 5}]
        self._secrets = {}

    def get_runs(self, owner, repo, limit=5):
        return self._runs

    def get_variable(self, owner, repo, name):
        return "600519,600036"  # 2 只 → 预估 $0.04

    def set_variable(self, owner, repo, name, value):
        raise AssertionError("不应被调用")

    def dispatch(self, owner, repo, ref="main", inputs=None):
        self._last_inputs = inputs

    def list_artifacts(self, owner, repo, per_page=10):
        return [{"id": 9, "name": "analysis-reports-5", "expired": False}]

    def download_artifact(self, owner, repo, artifact_id):
        return b""

    def list_secret_names(self, owner, repo):
        return list(self._secrets.keys())

    def set_secret(self, owner, repo, name, value):
        self._secrets[name] = value
        return None


def _make(runs=None):
    class _Cfg(cfg_mod.Config):
        def get_pat(self):
            pat = super().get_pat()
            return pat or ("pat-ok" if self.pat_enc else "")

    cfg = _Cfg()
    cfg.owner = "alice"
    cfg.repo = "dsa-cloud-alice"
    cfg.token = "tok123"
    cfg.pat_enc = "x"
    git = FakeGit(cfg, runs=runs)
    app = srv.create_app(cfg, static_dir=Path("__nonexistent__"), client_factory=lambda c: git)
    return TestClient(app), cfg, git


# ---------------- 网络层 ----------------

def test_network_settings_persist_and_passed_to_client():
    client, cfg, _ = _make()
    r = client.post("/api/network?token=tok123", headers={"X-Origin-Token": "tok123"},
                    json={"github_proxy": "http://127.0.0.1:7890", "github_ca_bundle": ""})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert cfg.github_proxy == "http://127.0.0.1:7890"


# ---------------- 可观测性 ----------------

def test_status_records_success_and_not_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    runs = [{"id": 7, "status": "completed", "conclusion": "success"}]
    client, _, _ = _make(runs=runs)
    r = client.get("/api/status?token=tok123").json()
    assert r["last_success_ts"] > 0
    assert r["stale"] is False
    assert r["running"] is False


def test_status_records_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    runs = [{"id": 8, "status": "completed", "conclusion": "failure"}]
    client, _, _ = _make(runs=runs)
    r = client.get("/api/status?token=tok123").json()
    assert r["last_failure_ts"] > 0
    assert r["last_success_ts"] == 0


def test_status_stale_when_no_success(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    client, _, _ = _make(runs=[])
    r = client.get("/api/status?token=tok123").json()
    assert r["stale"] is True


# ---------------- 成本护栏 ----------------

def test_trigger_ok_accumulates_spend():
    client, _, git = _make()
    r = client.post("/api/trigger?token=tok123", headers={"X-Origin-Token": "tok123"},
                    json={"mode": "full"}).json()
    assert r["ok"] is True
    assert r["estimated_usd"] == pytest.approx(0.04, abs=1e-6)
    assert r["projected_today_usd"] >= 0.04


def test_trigger_blocked_when_over_budget():
    client, cfg, _ = _make()
    cfg.budget_daily_usd = 0.01  # 单只预估 0.02 > 0.01
    cfg.budget_mode = "block"
    r = client.post("/api/trigger?token=tok123", headers={"X-Origin-Token": "tok123"},
                    json={"mode": "full"})
    assert r.status_code == 429
    assert r.json()["error"] == "budget_exceeded"


def test_trigger_warn_mode_allows_over_budget():
    client, cfg, _ = _make()
    cfg.budget_daily_usd = 0.01
    cfg.budget_mode = "warn"
    r = client.post("/api/trigger?token=tok123", headers={"X-Origin-Token": "tok123"},
                    json={"mode": "full"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_budget_get_and_post():
    client, _, _ = _make()
    assert client.get("/api/budget?token=tok123").json()["budget_mode"] in ("warn", "block")
    r = client.post("/api/budget?token=tok123", headers={"X-Origin-Token": "tok123"},
                    json={"budget_daily_usd": 5.0, "budget_mode": "block"})
    assert r.json()["budget_daily_usd"] == 5.0
    assert r.json()["budget_mode"] == "block"


# ---------------- 密钥(Secrets) ----------------

def test_secrets_list_requires_token():
    client, _, _ = _make()
    assert client.get("/api/secrets").status_code == 403


def test_secrets_list_returns_names():
    client, _, git = _make()
    git._secrets = {"TUSHARE_TOKEN": "x", "DEEPSEEK_API_KEY": "y"}
    r = client.get("/api/secrets?token=tok123").json()
    assert set(r["names"]) == {"TUSHARE_TOKEN", "DEEPSEEK_API_KEY"}


def test_secrets_set_requires_origin():
    client, _, _ = _make()
    assert client.post("/api/secrets?token=tok123",
                       json={"name": "X", "value": "v"}).status_code == 403


def test_secrets_set_calls_git():
    client, _, git = _make()
    r = client.post("/api/secrets?token=tok123", headers={"X-Origin-Token": "tok123"},
                    json={"name": "TUSHARE_TOKEN", "value": "abc"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert git._secrets.get("TUSHARE_TOKEN") == "abc"


def test_secrets_set_rejects_empty():
    client, _, _ = _make()
    r = client.post("/api/secrets?token=tok123", headers={"X-Origin-Token": "tok123"},
                    json={"name": "", "value": ""})
    assert r.status_code == 400


# ---------------- state_store 纯逻辑 ----------------

def test_record_run_outcome_and_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    st = {"last_success_ts": 0, "last_failure_ts": 0, "last_checked_ts": 0}
    ss.record_run_outcome(st, {"id": 3, "status": "completed", "conclusion": "success"})
    assert st["last_success_ts"] > 0 and not ss.is_stale(st)
    st2 = {"last_success_ts": 0}
    assert ss.is_stale(st2) is True


def test_spend_rollover_and_accumulate(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    assert ss.today_spend() == 0.0
    s1 = ss.add_spend(0.1)
    assert abs(s1 - 0.1) < 1e-6
    s2 = ss.add_spend(0.2)
    assert abs(s2 - 0.3) < 1e-6


def test_estimate_cost_floor_one_stock():
    assert ss.estimate_cost(0) == ss.estimate_cost(1)
    assert ss.estimate_cost(5) == pytest.approx(0.1, abs=1e-6)
