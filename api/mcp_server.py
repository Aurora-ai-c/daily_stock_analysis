# -*- coding: utf-8 -*-
"""MCP server 组装:认证→scope→限流→执行→审计。"""
from __future__ import annotations

import contextvars
import json
import logging
import time
from typing import Any, Callable, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from api.mcp_auth import RateLimiter, authenticate, is_mcp_enabled, load_keys, scope_for
from api.mcp_tools import (
    McpScopeError,
    TOOLS_SPEC,
    params_hash,
    query_bar_history,
    query_fundamental,
    query_quote,
    get_pipeline_status,
    get_screening_summary,
    get_signal_history,
    list_analysis_history,
    trigger_analysis,
)
from src.services.run_diagnostics import McpCallDiagnostic

# ---------------------------------------------------------------------------
# Module-level constants and state
# ---------------------------------------------------------------------------
GLOBAL_LIMITER = RateLimiter(rate=10.0, capacity=10.0)
TRIGGER_LIMITER = RateLimiter(rate=1.0 / 60.0, capacity=1.0)

# ContextVar to carry the authenticated key_id across the SSE request lifecycle
_current_key_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mcp_key_id", default="unknown"
)

# Audit log file (relative to CWD)
_AUDIT_LOG_PATH = "data/mcp_audit.log"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------
def map_error(exc: Exception) -> tuple[int, str]:
    """Map exceptions to JSON-RPC error codes per spec."""
    if isinstance(exc, ValidationError):
        return -32602, str(exc).splitlines()[0][:200]
    if isinstance(exc, (McpScopeError, PermissionError)):
        return -32001, str(exc)[:200]
    return -32603, str(exc)[:200]


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------
def _append_audit(rec: McpCallDiagnostic) -> None:
    """Append a single-line JSON audit record to the local log file."""
    try:
        import os
        os.makedirs(os.path.dirname(_AUDIT_LOG_PATH), exist_ok=True)
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            # Use sanitize() if available (Part-B DiagnosticRecord), else model_dump
            data = rec.sanitize() if hasattr(rec, "sanitize") else rec.model_dump(
                exclude={"params"}, exclude_none=True
            )
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - audit must never break the request
        pass


def _get_current_key_id() -> str:
    """Retrieve the current request's authenticated key_id from context."""
    return _current_key_id.get()


def _audit(
    key_id: str,
    tool: str,
    params: dict,
    start: float,
    status: str,
    success: bool,
) -> None:
    """Create and append an McpCallDiagnostic record."""
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


# ---------------------------------------------------------------------------
# FastMCP server builder
# ---------------------------------------------------------------------------
class _SSEAuthMiddleware:
    """Starlette middleware to extract Authorization header for MCP SSE streams."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract Authorization header
        headers = dict(scope.get("headers", []))
        auth_header = None
        if b"authorization" in headers:
            auth_header = headers[b"authorization"].decode("latin-1")
        elif b"Authorization" in headers:
            auth_header = headers[b"Authorization"].decode("latin-1")

        key_id = "unknown"
        if auth_header:
            key_id = authenticate(auth_header) or "unknown"

        # Set contextvar for the duration of this request
        token = _current_key_id.set(key_id)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_key_id.reset(token)


def build_mcp_server(
    manager: Any,
    svc: dict,
    runner: Callable[..., dict],
    repo: Optional[Any],
) -> FastMCP:
    """
    Build a FastMCP server with auth, rate limiting, scope enforcement, and audit.

    Args:
        manager: DataFetcherManager instance (or compatible) for quote/bar/fundamental.
        svc: Dictionary of services (screening, signals, history).
        runner: Callable(mode, date) -> dict with run_id; must acquire market review lock.
        repo: Repository instance for pipeline status.

    Returns:
        Configured FastMCP instance (not yet mounted).
    """
    mcp = FastMCP("dsa")

    # Adapters for real manager methods
    def _fundamental_adapter(code: str, market: str, mgr: Any) -> Any:
        """Adapt manager.get_fundamental_context(stock_code) -> FundamentalRaw-compatible."""
        # Real manager has get_fundamental_context(stock_code); brief expects (code, market)
        # Try both signatures for compatibility
        try:
            raw = mgr.get_fundamental_context(code)
        except TypeError:
            raw = mgr.get_fundamental_context(code, market)
        if raw is None:
            return {}
        # If already a FundamentalRaw, return as-is; else assume dict
        if hasattr(raw, "model_dump"):
            return raw.model_dump()
        return raw

    # Handler registry with manager/svc/repo/runner bound via closures
    handlers = {
        "query_quote": lambda a, m: query_quote(a, m, manager=manager),
        "query_bar_history": lambda a, m, d=60: query_bar_history(
            a, m, days=d, manager=manager
        ),
        "query_fundamental": lambda a, m: query_fundamental(
            a, m, manager=_fundamental_adapter
        ),
        "get_screening_summary": lambda: get_screening_summary(svc=svc),
        "get_signal_history": lambda c, l=10: get_signal_history(
            c, limit=l, svc=svc
        ),
        "list_analysis_history": lambda l=10: list_analysis_history(
            limit=l, svc=svc
        ),
        "trigger_analysis": lambda mode="full", date=None: trigger_analysis(
            mode=mode, date=date, runner=runner
        ),
        "get_pipeline_status": lambda: get_pipeline_status(repo=repo),
    }

    # Register each tool with its own closure-bound values (fix late-binding via factory)
    def _make_wrapper(
        _name: str, _fn: Callable, _required_scope: str, _limiter: RateLimiter
    ) -> Callable:
        @mcp.tool(name=_name)
        def tool_wrapper(**kwargs: Any) -> Any:
            key_id = _get_current_key_id()
            scopes = scope_for(key_id)
            if _required_scope not in scopes:
                raise McpScopeError(f"scope {_required_scope!r} required")
            if not _limiter.allow():
                raise PermissionError("rate limit exceeded")

            start = time.monotonic()
            try:
                result = _fn(**kwargs)
                _audit(key_id, _name, kwargs, start, "ok", True)
                return result
            except Exception as exc:  # noqa: BLE001
                code, message = map_error(exc)
                _audit(key_id, _name, kwargs, start, f"err:{code}", False)
                raise

        return tool_wrapper

    for spec in TOOLS_SPEC:
        name = spec["name"]
        fn = handlers[name]
        required_scope = spec["required_scope"]
        limiter = TRIGGER_LIMITER if name == "trigger_analysis" else GLOBAL_LIMITER
        _make_wrapper(name, fn, required_scope, limiter)

    # Wrap the SSE app with authentication middleware
    sse_app = _create_sse_app(mcp)
    sse_app.add_middleware(_SSEAuthMiddleware)
    mcp._sse_app = sse_app  # type: ignore[attr-defined]

    return mcp


def _create_sse_app(mcp: FastMCP):
    """Create a Starlette app for MCP SSE transport (extracted from run_sse_async)."""
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route

    from mcp.server.sse import SseServerTransport

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp._mcp_server.run(
                streams[0],
                streams[1],
                mcp._mcp_server.create_initialization_options(),
            )

    starlette_app = Starlette(
        debug=mcp.settings.debug,
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )
    return starlette_app