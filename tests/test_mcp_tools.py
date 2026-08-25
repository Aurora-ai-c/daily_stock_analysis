# -*- coding: utf-8 -*-
"""MCP 工具层测试:scope 装饰器 + TOOLS_SPEC + params_hash 审计。"""
from __future__ import annotations

import pytest

from api.mcp_tools import TOOLS_SPEC, McpScopeError, params_hash, require_scope


class _FakeManager:
    def get_realtime_quote(self, code, market):
        return {"code": code, "market": market, "price": 10.5}

    def get_daily_data(self, code, days=60):
        return None


class TestScopeDecorator:
    def test_scope_enforced(self):
        @require_scope("read:status")
        def tool():
            return "ok"

        assert tool("alice", {"read:status"}) == "ok"
        with pytest.raises(McpScopeError):
            tool("alice", {"read:basic"})


class TestToolSpec:
    def test_requires_scope_field(self):
        for spec in TOOLS_SPEC:
            assert "required_scope" in spec
            assert spec["required_scope"] in {
                "read:basic", "read:sensitive", "read:status", "write:trigger"}

    def test_has_eight_tools(self):
        assert len(TOOLS_SPEC) == 8

    def test_schema_imported_from_contracts(self):
        from data_provider.contracts import Bar, FundamentalRaw, Quote
        assert any(s["name"] == "query_quote" for s in TOOLS_SPEC)
        assert any(s["name"] == "query_bar_history" for s in TOOLS_SPEC)
        assert any(s["name"] == "query_fundamental" for s in TOOLS_SPEC)


class TestParamsHash:
    def test_stable_across_key_order(self):
        assert params_hash({"a": 1, "b": 2}) == params_hash({"b": 2, "a": 1})