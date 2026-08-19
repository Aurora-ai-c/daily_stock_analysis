import json
import pytest
from pathlib import Path
from src.services.pipeline.renderer import render_report, RendererArtifact
from src.services.pipeline.pusher import push_report, PusherArtifact


class _FakeChannel:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail
        self.calls = 0

    def send(self, payload):
        self.calls += 1
        if self.fail:
            raise RuntimeError("channel down")


class TestRenderer:
    def test_writes_artifact(self, tmp_path):
        art = render_report(tmp_path, {"title": "t"})
        assert isinstance(art, RendererArtifact)
        assert (tmp_path / "report.md").exists()


class TestPusher:
    def test_channel_failures_recorded(self, tmp_path):
        art = RendererArtifact(report_path=str(tmp_path / "report.md"), format="md",
                               render_latency=0.1)
        ok = _FakeChannel("feishu")
        bad = _FakeChannel("pushplus", fail=True)
        out = push_report(art, channels=[ok, bad])
        assert ok.calls >= 1
        assert any(f.startswith("pushplus") for f in out.failures)
        assert out.per_channel_status["feishu"] == "ok"

    def test_schema_version(self, tmp_path):
        art = RendererArtifact(report_path=str(tmp_path / "report.md"), format="md",
                               render_latency=0.1)
        out = push_report(art, channels=[])
        assert out.schema_version == 1