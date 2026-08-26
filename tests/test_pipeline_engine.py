# -*- coding: utf-8 -*-
from types import SimpleNamespace

import pytest

from src.services.pipeline.engine import (
    PipelineConcurrencyError,
    PipelineEngine,
    ReplayMode,
)


class _FakeRepo:
    def __init__(self, chain_length=0):
        self.active = None
        self.runs = {}
        self.steps = []
        self.superseded = []
        self.chain_length = chain_length

    def find_active_run(self, *, mode, date):
        return self.active

    def create_run(self, **kw):
        self.runs[kw["run_id"]] = kw
        return type("R", (), kw)()

    def mark_superseded(self, run_id, by_run_id):
        self.superseded.append((run_id, by_run_id))
        self.active = None

    def superseded_chain_length(self, *, mode, date):
        return self.chain_length

    def add_step(self, **kw):
        self.steps.append(kw)

    def update_step_status(self, run_id, step, status, error=None,
                           degraded_reasons=None):
        for s in reversed(self.steps):
            if s["run_id"] == run_id and s["step"] == step:
                s["status"] = status
                if degraded_reasons is not None:
                    s["degraded_reasons"] = degraded_reasons
                break

    def update_run_status(self, *, run_id, status, error_summary=None,
                          completed_at=None):
        if run_id in self.runs:
            self.runs[run_id]["status"] = status
            if error_summary is not None:
                self.runs[run_id]["error_summary"] = error_summary
            if completed_at is not None:
                self.runs[run_id]["completed_at"] = completed_at

    def get_run(self, run_id):
        kw = self.runs.get(run_id)
        return type("R", (), kw)() if kw else None


class _FakeManager:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = 0

    def get_daily_data(self, code, **kw):
        self.calls += 1
        if not self.ok:
            raise RuntimeError("down")
        import pandas as pd
        return (pd.DataFrame([{"date": "2026-08-14", "open": 1, "high": 2,
                               "low": 0.5, "close": 1.5, "volume": 100}]),
                "fake_fetcher")


class TestEngine:
    def test_full_run_success(self, tmp_path):
        engine = PipelineEngine(repo=_FakeRepo(), runs_dir=tmp_path, manager=_FakeManager())
        run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"])
        assert engine.repo.runs[run_id]["status"] == "completed"

    def test_hard_fail_when_no_data(self, tmp_path):
        engine = PipelineEngine(repo=_FakeRepo(), runs_dir=tmp_path, manager=_FakeManager(ok=False))
        run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"])
        assert engine.repo.runs[run_id]["status"] == "failed"
        assert "no data" in engine.repo.runs[run_id]["error_summary"]

    def test_side_effect_free_replays_failed_run(self, tmp_path):
        engine = PipelineEngine(repo=_FakeRepo(), runs_dir=tmp_path, manager=_FakeManager())
        first_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"])
        engine.repo.runs[first_id]["status"] = "failed"
        run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"],
                            replay=ReplayMode.SIDE_EFFECT_FREE, run_id=first_id)
        assert run_id == first_id
        assert engine.repo.runs[first_id]["status"] == "completed"
        assert not any(s["step"] == "pusher" and s["status"] == "ok" for s in engine.repo.steps)

    def test_side_effect_free_requires_failed_run(self, tmp_path):
        engine = PipelineEngine(repo=_FakeRepo(), runs_dir=tmp_path, manager=_FakeManager())
        first_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"])
        with pytest.raises(PipelineConcurrencyError):
            engine.run(mode="full", date="2026-08-16", stock_codes=["600519"],
                       replay=ReplayMode.SIDE_EFFECT_FREE, run_id=first_id)

    def test_active_run_reused_without_force(self, tmp_path):
        repo = _FakeRepo()
        repo.active = SimpleNamespace(run_id="existing-1")
        manager = _FakeManager()
        engine = PipelineEngine(repo=repo, runs_dir=tmp_path, manager=manager)
        run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"])
        assert run_id == "existing-1"
        assert repo.steps == []       # 不执行任何步骤(不 add_step)
        assert manager.calls == 0     # collector 未被调用
        assert "existing-1" not in repo.runs  # 不新建 run 记录

    def test_reused_active_run_does_not_push(self, tmp_path):
        repo = _FakeRepo()
        repo.active = SimpleNamespace(run_id="existing-1")

        class _BadChannel:
            name = "bad"

            def send(self, payload):
                raise AssertionError("复用路径不应推送")

        engine = PipelineEngine(repo=repo, runs_dir=tmp_path, manager=_FakeManager(),
                                channels=[_BadChannel()])
        run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"])
        assert run_id == "existing-1"
        assert repo.steps == []

    def test_force_supersedes_active_run(self, tmp_path):
        repo = _FakeRepo()
        repo.active = SimpleNamespace(run_id="old-1")
        engine = PipelineEngine(repo=repo, runs_dir=tmp_path, manager=_FakeManager())
        run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"], force=True)
        assert run_id != "old-1"
        assert repo.superseded == [("old-1", run_id)]
        assert repo.runs[run_id]["status"] == "completed"

    def test_superseded_chain_limit_rejects_force(self, tmp_path):
        repo = _FakeRepo(chain_length=5)
        repo.active = SimpleNamespace(run_id="old-1")
        engine = PipelineEngine(repo=repo, runs_dir=tmp_path, manager=_FakeManager())
        with pytest.raises(PipelineConcurrencyError):
            engine.run(mode="full", date="2026-08-16", stock_codes=["600519"], force=True)

    def test_probe_soft_fail_continues(self, tmp_path):
        import src.services.pipeline.engine as engine_mod
        repo = _FakeRepo()
        engine = PipelineEngine(repo=repo, runs_dir=tmp_path, manager=_FakeManager())
        orig = engine_mod.probe

        def boom(*a, **k):
            raise RuntimeError("probe down")

        engine_mod.probe = boom
        try:
            run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"])
        finally:
            engine_mod.probe = orig
        assert repo.runs[run_id]["status"] == "completed"
        steps = {s["step"]: s for s in repo.steps}
        assert steps["probe"]["status"] == "degraded"
        assert "probe down" in steps["probe"]["degraded_reasons"]

    def test_pusher_failures_mapped_to_degraded(self, tmp_path):
        import src.services.pipeline.pusher as pusher_mod
        repo = _FakeRepo()

        class _BadChannel:
            name = "bad"

            def send(self, payload):
                raise RuntimeError("push failed")

        pusher_mod.BACKOFF_SECONDS = (0.0, 0.0, 0.0)
        try:
            engine = PipelineEngine(repo=repo, runs_dir=tmp_path, manager=_FakeManager(),
                                    channels=[_BadChannel()])
            run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"])
        finally:
            pusher_mod.BACKOFF_SECONDS = (1.0, 4.0, 16.0)
        assert repo.runs[run_id]["status"] == "completed"
        steps = {s["step"]: s for s in repo.steps}
        assert steps["pusher"]["status"] == "degraded"
        assert "push failed" in steps["pusher"]["degraded_reasons"]
        assert steps["collector"]["status"] == "ok"
        assert steps["renderer"]["status"] == "ok"
class TestWiring:
    """I-2 生产链贯通:collector bars → probe 信号 → 报告章节。"""

    @staticmethod
    def _signal_manager():
        import pandas as pd

        closes = [10.0] * 24 + [12.0]
        volumes = [100] * 24 + [400]

        class _Mgr:
            def get_daily_data(self, code, **kw):
                df = pd.DataFrame({
                    "date": [f"2026-08-{i:02d}" for i in range(1, 26)],
                    "open": closes, "high": [c + 0.5 for c in closes],
                    "low": [c - 0.5 for c in closes], "close": closes,
                    "volume": volumes,
                })
                return (df, "fake")

        return _Mgr()

    def test_probe_receives_real_bars(self, tmp_path):
        repo = _FakeRepo()
        engine = PipelineEngine(repo=repo, runs_dir=tmp_path,
                                manager=self._signal_manager())
        run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"])
        assert repo.runs[run_id]["status"] == "completed"
        steps = {s["step"]: s for s in repo.steps}
        # collector 与 probe 均 ok(而非 degraded)
        assert steps["collector"]["status"] == "ok"
        assert steps["probe"]["status"] == "ok"
        # probe artifact 落盘且含真实信号
        import json
        from pathlib import Path

        art_files = sorted(Path(tmp_path / run_id).glob("step_*_probe.json"))
        assert art_files, "probe artifact must be persisted"
        art = json.loads(art_files[0].read_text(encoding="utf-8"))
        assert "600519" in art["candidates"]
        names = {s["signal"] for s in art["signals"]}
        assert {"ma_cross", "volume_surge", "breakout"} <= names

    def test_report_contains_structured_sections(self, tmp_path):
        from pathlib import Path

        repo = _FakeRepo()
        engine = PipelineEngine(repo=repo, runs_dir=tmp_path,
                                manager=self._signal_manager())
        run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"])
        report = (Path(tmp_path) / run_id / "report.md").read_text(encoding="utf-8")
        assert f"DSA 管线报告 full 2026-08-16" in report
        assert "## 数据采集 (collector)" in report
        assert "## 信号探针 (probe)" in report
        assert "## 交叉验证 (validated)" in report
        assert '"resolution"' in report
