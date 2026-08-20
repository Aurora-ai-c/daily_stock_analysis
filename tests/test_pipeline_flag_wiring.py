# -*- coding: utf-8 -*-
"""PIPELINE_V2_ENABLED flag 接线测试:flag 默认关闭 / env 开启 / 触发端点走新管线。"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config import Config


def test_flag_defaults_off(monkeypatch):
    """flag 默认关闭:裸 Config() 走 dataclass 默认值 False(旧路径不变)。"""
    monkeypatch.delenv("PIPELINE_V2_ENABLED", raising=False)
    cfg = Config()
    assert cfg.pipeline_v2_enabled is False


def test_flag_env_on(monkeypatch):
    """env 开启:Config() 不读 env,须经 _load_from_env()(与 brief 测试的契约偏差,见报告)。"""
    monkeypatch.setenv("PIPELINE_V2_ENABLED", "true")
    cfg = Config._load_from_env()
    assert cfg.pipeline_v2_enabled is True


def test_market_review_flag_on_runs_pipeline_v2(tmp_path, monkeypatch):
    """flag 开时 market_review 触发端点走 PipelineEngine:run_id + 产物 + 两表落库。"""
    try:
        from api.v1.endpoints.analysis import trigger_market_review
        from api.v1.schemas.analysis import MarketReviewRequest
    except Exception:  # pragma: no cover - optional dependency environments
        pytest.skip("analysis endpoint helpers unavailable in this environment")

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "smoke.db"))
    monkeypatch.setenv("PIPELINE_V2_ENABLED", "true")
    Config.reset_instance()
    from src.storage import DatabaseManager
    DatabaseManager.reset_instance()

    config = Config._load_from_env()
    assert config.pipeline_v2_enabled is True

    class _StubManager:
        def get_daily_data(self, code, **kw):
            return None, "stub"

    try:
        with patch("data_provider.base.DataFetcherManager", return_value=_StubManager()):
            response = trigger_market_review(
                request=MarketReviewRequest(send_notification=False),
                config=config,
            )
        assert response.status_code == 202, response.body
        payload = json.loads(response.body)
        assert payload.get("pipeline_v2") is True
        run_id = payload["run_id"]
        assert run_id

        from src.services.pipeline.repository import PipelineRepository, ensure_pipeline_tables
        ensure_pipeline_tables()
        repo = PipelineRepository()
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        steps = repo.steps_for(run_id)
        assert len(steps) == 5
        runs_dir = Path(config.database_path).parent / "pipeline" / "runs"
        assert (runs_dir / run_id / "report.md").exists()
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()