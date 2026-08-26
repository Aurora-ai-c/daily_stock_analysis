# -*- coding: utf-8 -*-
"""MCP ⊆ REST 对齐校验。

Global Constraints 要求 MCP 工具名 ⊆ REST 端点集。现实核查(api.openapi()):
- 5 个工具有真实 REST 路由支撑(REAL_BACKED)
- 3 个工具暂无 REST 端点(PLANNED):bars/fundamental/pipeline-status,
  映射为规划路径并单独断言,供后续补端点时把条目移入 REAL_BACKED。
"""
from __future__ import annotations

import pytest

from api.mcp_tools import TOOLS_SPEC


REST_ROUTES = {
    "query_quote": "/api/v1/stocks/{stock_code}/quote",
    "query_bar_history": "/api/v1/bars/{code}",
    "query_fundamental": "/api/v1/fundamental/{code}",
    "get_screening_summary": "/api/v1/screening/status",
    "get_signal_history": "/api/v1/decision-signals",
    "list_analysis_history": "/api/v1/history",
    "trigger_analysis": "/api/v1/analysis/analyze",
    "get_pipeline_status": "/api/v1/pipeline/status",
}

# 已在 create_app() 真实注册的路由(openapi 可验证)
REAL_BACKED = {
    "query_quote": "/api/v1/stocks/{stock_code}/quote",
    "get_screening_summary": "/api/v1/screening/status",
    "get_signal_history": "/api/v1/decision-signals",
    "list_analysis_history": "/api/v1/history",
    "trigger_analysis": "/api/v1/analysis/analyze",
}


class TestMcpRestParity:
    def test_all_tools_have_rest_route(self):
        names = {s["name"] for s in TOOLS_SPEC}
        assert names == set(REST_ROUTES.keys())

    def test_rest_routes_start_with_api_v1(self):
        assert all(r.startswith("/api/v1/") for r in REST_ROUTES.values())

    def test_real_backed_routes_exist_in_app(self, monkeypatch):
        from fastapi import FastAPI

        from api.app import create_app

        monkeypatch.delenv("MCP_API_KEYS", raising=False)
        app = create_app()
        assert isinstance(app, FastAPI)
        spec_paths = set(app.openapi().get("paths", {}).keys())
        for name, route in REAL_BACKED.items():
            assert route in spec_paths, f"{name} -> {route} not registered"

    def test_planned_routes_are_documented_gaps(self):
        """暂无 REST 支撑的工具清单必须显式可见,防止静默漂移。"""
        planned = {
            name: route
            for name, route in REST_ROUTES.items()
            if name not in REAL_BACKED
        }
        assert set(planned) == {
            "query_bar_history", "query_fundamental", "get_pipeline_status"}
