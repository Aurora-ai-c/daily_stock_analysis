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