# -*- coding: utf-8 -*-
"""PIPELINE_V2_ENABLED flag 接线测试:flag 默认开启 / env 可关 / 触发端点走新管线。"""

import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
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


class _SyncTaskQueue:
    """同步执行后台任务的假队列:端点契约测试只关心提交与产物,不测线程调度。"""

    def __init__(self):
        self.submitted = []
        self.results = []

    def submit_background_task(self, run_task, **kwargs):
        self.submitted.append(kwargs)
        task_id = kwargs.get("task_id") or "sync-task"
        self.results.append(run_task())
        return SimpleNamespace(task_id=task_id, trace_id="trace-test")


def test_market_review_flag_on_runs_pipeline_v2(tmp_path, monkeypatch):
    """flag 开时 market_review 触发端点提交后台任务并立即返回 202 + task_id。"""
    try:
        from api.v1.endpoints import analysis as analysis_endpoint_module
        from api.v1.endpoints.analysis import trigger_market_review
        from api.v1.schemas.analysis import MarketReviewAccepted, MarketReviewRequest
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

    sync_queue = _SyncTaskQueue()

    try:
        with patch("api.v1.endpoints.analysis.DataFetcherManager", new=_StubManager), \
                patch("api.v1.endpoints.analysis.get_task_queue", new=lambda: sync_queue):
            response = trigger_market_review(
                request=MarketReviewRequest(send_notification=False),
                config=config,
            )
            # stub 必须安装在端点模块的真实绑定点上(经 from ... import 绑定),
            # 且被 collector 实际消费,否则为死代码
            assert analysis_endpoint_module.DataFetcherManager is _StubManager
            assert _StubManager.calls >= 1
        # 端点契约与 v1 对齐:202 + MarketReviewAccepted,管线在后台任务中执行
        assert isinstance(response, MarketReviewAccepted)
        assert response.status == "accepted"
        assert response.task_id
        assert response.pipeline_v2 is True
        assert sync_queue.submitted, "v2 分支必须提交后台任务"

        from src.services.pipeline.repository import PipelineRepository, ensure_pipeline_tables
        ensure_pipeline_tables()
        repo = PipelineRepository()
        today = datetime.now().strftime("%Y-%m-%d")
        active = repo.find_active_run(mode="market_review", date=today)
        assert active is not None
        run_id = active.run_id
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        steps = repo.steps_for(run_id)
        assert len(steps) == 5
        runs_dir = Path(config.database_path).parent / "pipeline" / "runs"
        assert (runs_dir / run_id / "report.md").exists()

        # 并发去重(用户裁定):同 (mode,date) 已有 active run → 复用 run_id,不重跑、不追加 step 行
        with patch("api.v1.endpoints.analysis.DataFetcherManager", new=_StubManager), \
                patch("api.v1.endpoints.analysis.get_task_queue", new=lambda: sync_queue):
            response2 = trigger_market_review(
                request=MarketReviewRequest(send_notification=False),
                config=config,
            )
        assert response2.task_id
        assert response2.task_id != response.task_id
        assert len(repo.steps_for(run_id)) == 5
        assert repo.get_run(run_id).run_id == run_id

        # 轮询契约与 v1 对齐:任务结果携带复盘正文(status 端点从 result["result"] 提取)
        first_result = sync_queue.results[0]
        assert isinstance(first_result, dict)
        report_text = first_result["result"]
        assert isinstance(report_text, str) and report_text.strip()
        assert "market_review" in report_text  # v2 管线产物正文
        assert first_result["run_id"] == run_id
        assert first_result["pipeline_v2"] is True
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()