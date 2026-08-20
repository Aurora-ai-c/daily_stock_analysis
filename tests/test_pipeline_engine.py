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

    def get_daily_data(self, code, **kw):
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
        engine = PipelineEngine(repo=repo, runs_dir=tmp_path, manager=_FakeManager())
        run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"])
        assert run_id == "existing-1"
        assert "existing-1" not in repo.runs

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