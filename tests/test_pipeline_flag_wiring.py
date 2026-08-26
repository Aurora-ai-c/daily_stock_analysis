# -*- coding: utf-8 -*-
"""PIPELINE_V2_ENABLED flag 接线测试:flag 默认开启 / env 可关 / 触发端点走新管线。"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config import Config


def test_flag_defaults_on(monkeypatch):
    """flag 默认开启(用户裁定 2026-08-26);PIPELINE_V2_ENABLED=false 回退旧路径。"""
    monkeypatch.delenv("PIPELINE_V2_ENABLED", raising=False)
    cfg = Config()
    assert cfg.pipeline_v2_enabled is True


def test_flag_env_off(monkeypatch):
    """env 关闭:Config._load_from_env() 读 env,false 时回退旧路径。"""
    monkeypatch.setenv("PIPELINE_V2_ENABLED", "false")
    cfg = Config._load_from_env()
    assert cfg.pipeline_v2_enabled is False


def test_market_review_flag_on_runs_pipeline_v2(tmp_path, monkeypatch):
    """flag 开时 market_review 触发端点走 PipelineEngine:run_id + 产物 + 两表落库。"""
    try:
        from api.v1.endpoints import analysis as analysis_endpoint_module
        from api.v1.endpoints.analysis import trigger_market_review
        from api.v1.schemas.analysis import MarketReviewRequest
    except Exception:  # pragma: no cover - optional dependency environments
        pytest.skip("analysis endpoint helpers unavailable in this environment")

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "smoke.db"))
    monkeypatch.setenv("PIPELINE_V2_ENABLED", "true")
    # 显式固化 STOCK_LIST,避免依赖环境/.env 链导致测试走向真实网络;
    # 取固定非空值而非空串:空串会让 collector 跳过 manager 调用,使下方 stub 生效断言无法成立
    monkeypatch.setenv("STOCK_LIST", "600519")
    Config.reset_instance()
    from src.storage import DatabaseManager
    DatabaseManager.reset_instance()

    config = Config._load_from_env()
    assert config.pipeline_v2_enabled is True

    class _StubManager:
        calls = 0

        def get_daily_data(self, code, **kw):
            _StubManager.calls += 1
            return None, "stub"

    try:
        with patch("api.v1.endpoints.analysis.DataFetcherManager", new=_StubManager):
            response = trigger_market_review(
                request=MarketReviewRequest(send_notification=False),
                config=config,
            )
            # stub 必须安装在端点模块的真实绑定点上(经 from ... import 绑定),
            # 且被 collector 实际消费,否则为死代码
            assert analysis_endpoint_module.DataFetcherManager is _StubManager
            assert _StubManager.calls >= 1
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

        # 并发去重(用户裁定):同 (mode,date) 已有 active run → 复用 run_id,不重跑、不追加 step 行
        with patch("api.v1.endpoints.analysis.DataFetcherManager", new=_StubManager):
            response2 = trigger_market_review(
                request=MarketReviewRequest(send_notification=False),
                config=config,
            )
        payload2 = json.loads(response2.body)
        assert payload2["run_id"] == run_id
        assert payload2.get("reused") is True
        assert len(repo.steps_for(run_id)) == 5
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()