import pytest
from src.services.run_diagnostics import (
    DiagnosticRecord, PipelineStepDiagnostic, McpCallDiagnostic, UpdateEventDiagnostic,
)


class TestDiagnosticRecordBase:
    def test_to_dict_filters_none(self):
        d = PipelineStepDiagnostic(run_id="r1", step_name="probe", status="ok",
                                   latency_ms=None, artifact_path=None,
                                   error_sanitized=None)
        payload = d.to_dict()
        assert "latency_ms" not in payload
        assert payload["run_id"] == "r1"

    def test_sanitize_redacts_secrets(self):
        d = PipelineStepDiagnostic(run_id="r1", step_name="push", status="failed",
                                   error_sanitized="api_key=sk-12345")
        out = d.sanitize()
        assert "sk-12345" not in out["error_sanitized"]


class TestSubclasses:
    def test_mcp_record_fields(self):
        d = McpCallDiagnostic(key_id="alice", tool_name="query_quote",
                              remote_ip="127.0.0.1", params_hash="abc123",
                              latency_ms=5, status="ok", success=True)
        assert d.tool_name == "query_quote"

    def test_update_record_fields(self):
        d = UpdateEventDiagnostic(version="0.2.0", event="downloaded", status="ok")
        assert d.event == "downloaded"
