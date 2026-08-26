# -*- coding: utf-8 -*-
"""MCP server 测试:错误映射 + 构建冒烟。"""
from __future__ import annotations

import pytest
from data_provider.contracts import Quote

from api.mcp_server import build_mcp_server, map_error


class TestErrorMapping:
    def test_validation_to_32602(self):
        from pydantic import ValidationError
        try:
            Quote(code="x")  # missing required fields
        except ValidationError as exc:
            assert map_error(exc)[0] == -32602

    def test_scope_to_32001(self):
        from api.mcp_tools import McpScopeError
        assert map_error(McpScopeError("no"))[0] == -32001

    def test_internal_to_32603(self):
        assert map_error(RuntimeError("boom"))[0] == -32603


class TestServerBuild:
    def test_build_with_deps(self):
        server = build_mcp_server(
            manager=object(),
            svc={},
            runner=lambda **kw: {"run_id": "x"},
            repo=None
        )
        assert server is not None


def _hash(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()


class _FakeManager:
    def get_realtime_quote(self, code, market):
        return {"code": code, "market": market, "price": 10.5}

    def get_daily_data(self, code, days=60):
        return None

    def get_fundamental_context(self, stock_code):
        return {
            "fundamental": {
                "report_date": "2025-06-30",
                "fiscal_period": "Q2",
                "market": "sh",
                "total_assets": 1.0,
            }
        }


def _build(monkeypatch, tmp_path, audit_name="audit.log"):
    import os

    monkeypatch.setenv("MCP_API_KEYS", f"alice:{_hash('secret1')}")
    audit_path = tmp_path / audit_name
    monkeypatch.setattr(
        "api.mcp_server._AUDIT_LOG_PATH", str(audit_path), raising=False
    )
    server = build_mcp_server(
        manager=_FakeManager(),
        svc={"screening": lambda: {"count": 3}},
        runner=lambda **kw: {"run_id": "x"},
        repo=None,
    )
    return server, audit_path


class TestEndToEnd:
    """经 FastMCP.call_tool 的端到端行为测试(覆盖参数校验/scope/审计)。"""

    def test_query_quote_ok_and_audited(self, monkeypatch, tmp_path):
        import asyncio
        import json as _json

        from api.mcp_server import _current_key_id

        server, audit_path = _build(monkeypatch, tmp_path)
        token = _current_key_id.set("alice")
        try:
            result = asyncio.run(
                server.call_tool("query_quote", {"code": "600000", "market": "sh"})
            )
        finally:
            _current_key_id.reset(token)
        assert result, "expected TextContent output"
        assert audit_path.exists(), "audit log must be written"
        line = audit_path.read_text(encoding="utf-8").strip().splitlines()[0]
        rec = _json.loads(line)
        assert rec["tool_name"] == "query_quote"
        assert rec["success"] is True
        assert "params_hash" in rec and rec["params_hash"]
        # 原始参数不得入日志
        assert "600000" not in line

    def test_unknown_key_scope_rejected(self, monkeypatch, tmp_path):
        import asyncio

        from api.mcp_server import _current_key_id

        server, _ = _build(monkeypatch, tmp_path, "audit2.log")
        token = _current_key_id.set("unknown")
        try:
            with pytest.raises(Exception, match="[Ss]cope"):
                asyncio.run(
                    server.call_tool(
                        "query_quote", {"code": "600000", "market": "sh"}
                    )
                )
        finally:
            _current_key_id.reset(token)

    def test_sensitive_scope_required_for_fundamental(self, monkeypatch, tmp_path):
        import asyncio

        from api.mcp_server import _current_key_id

        server, _ = _build(monkeypatch, tmp_path, "audit3.log")
        # alice 默认只有 read:basic → fundamental 应被拒
        token = _current_key_id.set("alice")
        try:
            with pytest.raises(Exception, match="[Ss]cope"):
                asyncio.run(
                    server.call_tool(
                        "query_fundamental", {"code": "600000", "market": "sh"}
                    )
                )
        finally:
            _current_key_id.reset(token)

    def test_trigger_rate_limited(self, monkeypatch, tmp_path):
        import asyncio

        from api.mcp_auth import load_keys
        from api.mcp_server import TRIGGER_LIMITER, _current_key_id

        server, _ = _build(monkeypatch, tmp_path, "audit4.log")
        monkeypatch.setenv("MCP_KEY_ALICE_SCOPE", "write:trigger")
        assert "write:trigger" in load_keys()["alice"]
        TRIGGER_LIMITER.tokens = TRIGGER_LIMITER.capacity  # reset bucket
        token = _current_key_id.set("alice")
        try:
            first = asyncio.run(server.call_tool("trigger_analysis", {}))
            assert first
            with pytest.raises(Exception):
                asyncio.run(server.call_tool("trigger_analysis", {}))
        finally:
            _current_key_id.reset(token)