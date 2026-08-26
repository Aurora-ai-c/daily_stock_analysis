# -*- coding: utf-8 -*-
"""MCP server 组装:认证→scope→限流→执行→审计。

版本绑定:mcp==1.2.x(FastMCP 无 sse_app(),SSE app 需自建;
SseServerTransport 的 endpoint 不感知挂载前缀,需显式带 /mcp 前缀)。
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import time
from typing import Any, Callable, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ValidationError

from api.mcp_auth import RateLimiter, authenticate, scope_for
from api.mcp_tools import (
    McpScopeError,
    params_hash,
    get_pipeline_status,
    get_screening_summary,
    get_signal_history,
    list_analysis_history,
    query_bar_history,
    query_fundamental,
    query_quote,
    trigger_analysis,
)
from src.services.run_diagnostics import McpCallDiagnostic

GLOBAL_LIMITER = RateLimiter(rate=10.0, capacity=10.0)
TRIGGER_LIMITER = RateLimiter(rate=1.0 / 60.0, capacity=1.0)

# /mcp 挂载前缀:SseServerTransport 下发的 endpoint 不感知 root_path,
# 必须把前缀写进 endpoint 才能让客户端 POST 到正确路径。
_MCP_MOUNT_PREFIX = "/mcp"

_current_key_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mcp_key_id", default="unknown"
)

_AUDIT_LOG_PATH = "data/mcp_audit.log"

logger = logging.getLogger(__name__)


def map_error(exc: Exception) -> tuple[int, str]:
    """异常 → JSON-RPC 错误码(ValidationError→-32602/scope·权限→-32001/其他→-32603)。"""
    if isinstance(exc, ValidationError):
        return -32602, str(exc).splitlines()[0][:200]
    if isinstance(exc, (McpScopeError, PermissionError)):
        return -32001, str(exc)[:200]
    return -32603, str(exc)[:200]


def _append_audit(rec: McpCallDiagnostic) -> None:
    """追加单行 JSON 审计记录;写失败静默(审计不得破坏请求)。"""
    try:
        parent = os.path.dirname(_AUDIT_LOG_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        data = (
            rec.sanitize()
            if hasattr(rec, "sanitize")
            else rec.__dict__
        )
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001
        logger.debug("mcp audit write failed", exc_info=True)


def _get_current_key_id() -> str:
    return _current_key_id.get()


def _audit(
    key_id: str,
    tool: str,
    params: dict,
    start: float,
    status: str,
    success: bool,
) -> None:
    rec = McpCallDiagnostic(
        key_id=key_id,
        tool_name=tool,
        remote_ip="local",
        params_hash=params_hash(params),
        latency_ms=int((time.monotonic() - start) * 1000),
        status=status,
        success=success,
    )
    _append_audit(rec)


def _jsonable(result: Any) -> Any:
    """pydantic 模型/模型列表 → JSON 安全结构;其余透传。"""
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if isinstance(result, list) and result and all(isinstance(r, BaseModel) for r in result):
        return [r.model_dump(mode="json") for r in result]
    return result


def _guarded_call(
    name: str,
    required_scope: str,
    limiter: RateLimiter,
    fn: Callable[..., Any],
    kwargs: dict,
) -> Any:
    """统一执行链:scope 校验 → 限流 → 执行 → 审计。"""
    key_id = _get_current_key_id()
    scopes = scope_for(key_id)
    if key_id == "unknown" or required_scope not in scopes:
        raise McpScopeError(f"scope {required_scope!r} required")
    if not limiter.allow():
        raise PermissionError("rate limit exceeded")

    start = time.monotonic()
    try:
        result = fn(**kwargs)
        _audit(key_id, name, kwargs, start, "ok", True)
        return _jsonable(result)
    except Exception as exc:  # noqa: BLE001
        code, message = map_error(exc)
        logger.warning("[mcp] tool %s failed (%s): %s", name, code, message)
        _audit(key_id, name, kwargs, start, f"err:{code}", False)
        raise


class _SSEAuthMiddleware:
    """纯 ASGI 中间件:每个 HTTP 请求校验 Bearer key,失败即 401(fail-closed)。

    身份语义:工具执行发生在 GET /sse 的任务上下文中,会话内所有调用
    沿用建立连接时的身份(POST /messages/ 不执行工具)。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        key_id = authenticate(auth_header) if auth_header else None
        if key_id is None:
            response = _json_response(401, {"error": "unauthorized"})
            await response(scope, receive, send)
            return

        token = _current_key_id.set(key_id)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_key_id.reset(token)


def _json_response(status_code: int, payload: dict) -> Any:
    from starlette.responses import JSONResponse

    return JSONResponse(status_code=status_code, content=payload)


class _FundamentalManagerAdapter:
    """把真实 manager.get_fundamental_context(stock_code) 适配为
    工具层期望的 get_fundamental_data(code, market)。"""

    def __init__(self, mgr: Any) -> None:
        self._mgr = mgr

    def get_fundamental_data(self, code: str, market: str) -> dict:
        ctx = self._mgr.get_fundamental_context(code)
        if not ctx:
            raise ValueError(f"fundamental data unavailable for {code!r}")
        candidate = ctx.get("fundamental") if isinstance(ctx, dict) else None
        raw = candidate if isinstance(candidate, dict) else (
            ctx if isinstance(ctx, dict) else {}
        )
        raw = dict(raw)
        raw.setdefault("code", code)
        if "market" not in raw:
            raw["market"] = market
        return raw


def build_mcp_server(
    manager: Any,
    svc: dict,
    runner: Callable[..., dict],
    repo: Optional[Any],
) -> FastMCP:
    """构建含认证/scope/限流/审计的 FastMCP 实例(不挂载)。"""
    mcp = FastMCP("dsa")

    fundamental_manager = _FundamentalManagerAdapter(manager)

    def _svc_get(key: str, default):
        getter = (svc or {}).get(key)
        return getter() if callable(getter) else default

    # --- 显式具名参数注册(禁用 **kwargs:FastMCP 1.2.x 会把它当必填字段) ---
    @mcp.tool(name="query_quote")
    def query_quote_tool(code: str, market: str) -> Any:
        return _guarded_call(
            "query_quote", "read:basic", GLOBAL_LIMITER,
            lambda code, market: query_quote(code, market, manager=manager),
            {"code": code, "market": market},
        )

    @mcp.tool(name="query_bar_history")
    def query_bar_history_tool(code: str, market: str, days: int = 60) -> Any:
        return _guarded_call(
            "query_bar_history", "read:basic", GLOBAL_LIMITER,
            lambda code, market, days: query_bar_history(code, market, days=days, manager=manager),
            {"code": code, "market": market, "days": days},
        )

    @mcp.tool(name="query_fundamental")
    def query_fundamental_tool(code: str, market: str) -> Any:
        return _guarded_call(
            "query_fundamental", "read:sensitive", GLOBAL_LIMITER,
            lambda code, market: query_fundamental(code, market, manager=fundamental_manager),
            {"code": code, "market": market},
        )

    @mcp.tool(name="get_screening_summary")
    def get_screening_summary_tool() -> Any:
        return _guarded_call(
            "get_screening_summary", "read:status", GLOBAL_LIMITER,
            lambda: get_screening_summary(svc=svc),
            {},
        )

    @mcp.tool(name="get_signal_history")
    def get_signal_history_tool(code: str, limit: int = 10) -> Any:
        return _guarded_call(
            "get_signal_history", "read:status", GLOBAL_LIMITER,
            lambda code, limit: get_signal_history(code, limit=limit, svc=svc),
            {"code": code, "limit": limit},
        )

    @mcp.tool(name="list_analysis_history")
    def list_analysis_history_tool(limit: int = 10) -> Any:
        return _guarded_call(
            "list_analysis_history", "read:status", GLOBAL_LIMITER,
            lambda limit: list_analysis_history(limit=limit, svc=svc),
            {"limit": limit},
        )

    @mcp.tool(name="trigger_analysis")
    def trigger_analysis_tool(mode: str = "full", date: Optional[str] = None) -> Any:
        return _guarded_call(
            "trigger_analysis", "write:trigger", TRIGGER_LIMITER,
            lambda mode, date: trigger_analysis(mode=mode, date=date, runner=runner),
            {"mode": mode, "date": date},
        )

    @mcp.tool(name="get_pipeline_status")
    def get_pipeline_status_tool() -> Any:
        return _guarded_call(
            "get_pipeline_status", "read:status", GLOBAL_LIMITER,
            lambda: get_pipeline_status(repo=repo),
            {},
        )

    sse_app = _create_sse_app(mcp)
    sse_app.add_middleware(_SSEAuthMiddleware)
    mcp._sse_app = sse_app  # type: ignore[attr-defined]

    return mcp


def _create_sse_app(mcp: FastMCP):
    """复刻 FastMCP.run_sse_async 的 Starlette 组装,但返回 app 而非启动 uvicorn。"""
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route

    from mcp.server.sse import SseServerTransport

    sse = SseServerTransport(f"{_MCP_MOUNT_PREFIX}/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp._mcp_server.run(
                streams[0],
                streams[1],
                mcp._mcp_server.create_initialization_options(),
            )

    return Starlette(
        debug=mcp.settings.debug,
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )
