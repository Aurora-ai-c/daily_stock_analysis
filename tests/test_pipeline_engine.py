# -*- coding: utf-8 -*-
import pytest
from src.services.pipeline.engine import PipelineEngine, ReplayMode


class _FakeRepo:
    def __init__(self):
        self.active = None
        self.runs = {}
        self.steps = []

    def find_active_run(self, mode, date):
        return self.active

    def create_run(self, **kw):
        self.runs[kw["run_id"]] = kw
        return type("R", (), kw)()

    def mark_superseded(self, run_id, by_run_id):
        self.active = None

    def add_step(self, **kw):
        self.steps.append(kw)

    def update_step_status(self, run_id, step, status, error=None):
        pass

    def get_run(self, run_id):
        return self.runs.get(run_id)


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

    def test_side_effect_free_skips_pusher(self, tmp_path):
        engine = PipelineEngine(repo=_FakeRepo(), runs_dir=tmp_path, manager=_FakeManager())
        run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"],
                            replay=ReplayMode.SIDE_EFFECT_FREE)
        steps = [s["step"] for s in engine.repo.steps]
        assert not any(s["step"] == "pusher" and s["status"] == "ok" for s in engine.repo.steps)