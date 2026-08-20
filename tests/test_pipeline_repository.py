import pytest
from src.services.pipeline.repository import PipelineRepository


class TestPipelineRepository:
    def test_create_and_get_run(self, tmp_path):
        repo = PipelineRepository(db_path=str(tmp_path / "t.db"))
        run = repo.create_run(run_id="r1", trigger="cron", mode="full", date="2026-08-16")
        got = repo.get_run("r1")
        assert got.mode == "full"

    def test_single_lock_semantics(self, tmp_path):
        repo = PipelineRepository(db_path=str(tmp_path / "t.db"))
        repo.create_run(run_id="r1", trigger="cron", mode="full", date="2026-08-16")
        active = repo.find_active_run(mode="full", date="2026-08-16")
        assert active is not None and active.run_id == "r1"
        repo.mark_superseded("r1", by_run_id="r2")
        assert repo.find_active_run(mode="full", date="2026-08-16") is None

    def test_add_step(self, tmp_path):
        repo = PipelineRepository(db_path=str(tmp_path / "t.db"))
        repo.create_run(run_id="r1", trigger="cron", mode="full", date="2026-08-16")
        repo.add_step(run_id="r1", step="probe", status="ok", artifact_path="/tmp/x.json")
        steps = repo.steps_for("r1")
        assert steps[0].step == "probe"

    def test_update_run_status(self, tmp_path):
        repo = PipelineRepository(db_path=str(tmp_path / "t.db"))
        repo.create_run(run_id="r1", trigger="cron", mode="full", date="2026-08-16")
        repo.update_run_status(run_id="r1", status="completed",
                               completed_at="2026-08-16T10:00:00")
        run = repo.get_run("r1")
        assert run.status == "completed"
        assert run.completed_at == "2026-08-16T10:00:00"

    def test_superseded_chain_length(self, tmp_path):
        repo = PipelineRepository(db_path=str(tmp_path / "t.db"))
        repo.create_run(run_id="r1", trigger="cron", mode="full", date="2026-08-16")
        repo.create_run(run_id="r2", trigger="cron", mode="full", date="2026-08-16")
        repo.mark_superseded("r1", by_run_id="r2")
        assert repo.superseded_chain_length(mode="full", date="2026-08-16") == 2

    def test_ensure_pipeline_tables_idempotent(self, tmp_path):
        from src.services.pipeline.repository import ensure_pipeline_tables
        repo = PipelineRepository(db_path=str(tmp_path / "t.db"))
        ensure_pipeline_tables(manager=repo.db)
        repo.create_run(run_id="r1", trigger="cron", mode="full", date="2026-08-16")
        assert repo.get_run("r1") is not None
