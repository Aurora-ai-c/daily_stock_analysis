# -*- coding: utf-8 -*-
"""MCP 工具层:scope 校验 + 8 工具 + 审计哈希。"""
from __future__ import annotations

import functools
import hashlib
import json
from typing import Any, Optional

from data_provider.contracts import Bar, FundamentalRaw, Quote

SCOPES = {"read:basic", "read:sensitive", "read:status", "write:trigger"}


class McpScopeError(PermissionError):
    pass


def require_scope(required: str):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(key_id: str, scopes: set[str], *args, **kwargs):
            if required not in scopes:
                raise McpScopeError(
                    f"key {key_id!r} lacks scope {required!r}")
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def params_hash(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@require_scope("read:basic")
def query_quote(code: str, market: str, manager=None) -> Quote:
    raw = manager.get_realtime_quote(code, market)
    return Quote(code=code, market=market, price=raw["price"])


@require_scope("read:basic")
def query_bar_history(code: str, market: str, days: int = 60, manager=None) -> list[Bar]:
    df = manager.get_daily_data(code, days=days)
    if df is None:
        return []
    return [Bar(date=str(r["date"]), open=float(r["open"]), high=float(r["high"]),
                low=float(r["low"]), close=float(r["close"]), volume=int(r["volume"]))
            for r in df.tail(days).to_dict("records")]


@require_scope("read:sensitive")
def query_fundamental(code: str, market: str, manager=None) -> FundamentalRaw:
    raw = manager.get_fundamental_data(code, market)
    return FundamentalRaw(**raw)


@require_scope("read:status")
def get_screening_summary(svc=None) -> dict:
    return (svc or {}).get("screening", lambda: {"count": 0})()


@require_scope("read:status")
def get_signal_history(code: str, limit: int = 10, svc=None) -> list[dict]:
    return (svc or {}).get("signals", lambda: [])()


@require_scope("read:status")
def list_analysis_history(limit: int = 10, svc=None) -> list[dict]:
    return (svc or {}).get("history", lambda: [])()


@require_scope("write:trigger")
def trigger_analysis(mode: str = "full", date: Optional[str] = None, runner=None) -> dict:
    return runner(mode=mode, date=date)


@require_scope("read:status")
def get_pipeline_status(repo=None) -> dict:
    return {"enabled": repo is not None}


TOOLS_SPEC = [
    {"name": "query_quote", "required_scope": "read:basic", "schema": Quote},
    {"name": "query_bar_history", "required_scope": "read:basic", "schema": Bar},
    {"name": "query_fundamental", "required_scope": "read:sensitive", "schema": FundamentalRaw},
    {"name": "get_screening_summary", "required_scope": "read:status", "schema": dict},
    {"name": "get_signal_history", "required_scope": "read:status", "schema": dict},
    {"name": "list_analysis_history", "required_scope": "read:status", "schema": dict},
    {"name": "trigger_analysis", "required_scope": "write:trigger", "schema": dict},
    {"name": "get_pipeline_status", "required_scope": "read:status", "schema": dict},
]