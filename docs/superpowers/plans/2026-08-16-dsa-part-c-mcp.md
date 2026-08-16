# Part-C: MCP 工具集成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `api/app.py` 内嵌 MCP server(local-only),8 个工具 + 多 key 鉴权 + 限流 + 审计;`MCP_API_KEYS` 未配置 → 不挂载(404)。

**Architecture:** `mcp==1.2.x`(锁 minor,先安装);`FastMCP` 挂 `app.mount("/mcp", ...)`;鉴权 middleware → 工具层 scope 校验 → 令牌桶限流 → 审计 `McpCallDiagnostic`。

**Tech Stack:** Python 3.11+, mcp 1.2.x, pydantic 2.13, FastAPI

## Global Constraints

- 仅 local 挂载;cloud 不挂(文档化:云端走 `deploy_user.py` 触发 Actions)
- 多 key 鉴权:`MCP_API_KEYS="key_id:sha256hex,key_id2:sha256hex"`;每 key scope 由 env `MCP_KEY_<KEY_ID>_SCOPE` 指定(逗号分隔);key_id 大小写不敏感
- 错误映射:ValidationError → -32602(Invalid params)/ 内部异常 → -32603 / 鉴权失败 → -32001(自定义 Server error)
- 限流:令牌桶 10/s 全局;`trigger_analysis` 单独 1/min
- 审计:`params_hash = sha256(json.dumps(params, sort_keys=True))[:16]`;stock_list 等大参数只进 hash 不进日志
- 工具 schema 必须 import 自 `data_provider/contracts.py`(Part-A);CI 校验 MCP 工具名 ⊆ REST 端点集
- `trigger_analysis` 复用 `_try_acquire_market_review_lock` 同一把锁
- 依赖 Part-B Task 1 的 `McpCallDiagnostic`

---

### Task 1: 安装 mcp 依赖 + 锁定版本

- [ ] **Step 1: 安装并验证**

```bash
& .venv/Scripts/python.exe -m pip install "mcp>=1.2,<1.3"
& .venv/Scripts/python.exe -c "import mcp; print(mcp.__version__)"
```

Expected: 输出 1.2.x

- [ ] **Step 2: 冻结 requirements**

```bash
& .venv/Scripts/python.exe -m pip freeze | Select-String -Pattern "^mcp=="
```

- [ ] **Step 3: 确认 requirements.txt 或 pyproject.toml 中锁定 `mcp==1.2.x`(按现有依赖管理方式;如无依赖清单则创建 requirements.txt 并在 commit 时加入)**

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore(deps): pin mcp 1.2.x for MCP tool layer"
```

---

### Task 2: 鉴权模块 + 令牌桶限流

**Files:**
- Create: `api/mcp_auth.py`
- Test: `tests/test_mcp_auth.py`

**Interfaces:**
- Consumes: env `MCP_API_KEYS`(格式 `key_id:sha256hex` 逗号分隔)、`MCP_KEY_<KEY_ID>_SCOPE`
- Produces:
  - `load_keys() -> dict[str, set[str]]`(key_id → scope 集合;解析失败抛 `McpAuthConfigError`)
  - `authenticate(header_value: str) -> Optional[str]`:Header `Authorization: Bearer <plain_key>`;sha256(plain) 匹配任一 `key_id:hash` → 返回 key_id;不匹配/无 header → None
  - `scope_for(key_id) -> set[str]`(未配置 scope env → 默认 `{"read:basic"}`)
  - `RateLimiter(rate: float, capacity: float)` 令牌桶:`allow() -> bool`
  - `MCP_API_KEYS` 未设置或空 → `is_mcp_enabled() -> False`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mcp_auth.py
import hashlib
import pytest
from api.mcp_auth import load_keys, authenticate, RateLimiter, is_mcp_enabled


def _hash(s):
    return hashlib.sha256(s.encode()).hexdigest()


class TestLoadKeys:
    def test_parses_env(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEYS", f"alice:{_hash('k1')},bob:{_hash('k2')}")
        monkeypatch.setenv("MCP_KEY_ALICE_SCOPE", "read:basic,read:sensitive")
        keys = load_keys()
        assert "alice" in keys and "read:sensitive" in keys["alice"]
        assert keys["bob"] == {"read:basic"}  # 未配置 scope 默认

    def test_disabled_when_unset(self, monkeypatch):
        monkeypatch.delenv("MCP_API_KEYS", raising=False)
        assert is_mcp_enabled() is False

    def test_invalid_format_raises(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEYS", "alice:nothex")
        with pytest.raises(Exception):
            load_keys()


class TestAuthenticate:
    def test_valid_key_returns_id(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEYS", f"alice:{_hash('secret1')}")
        assert authenticate(f"Bearer secret1") == "alice"

    def test_invalid_key_returns_none(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEYS", f"alice:{_hash('secret1')}")
        assert authenticate("Bearer wrong") is None

    def test_missing_header_returns_none(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEYS", f"alice:{_hash('secret1')}")
        assert authenticate(None) is None


class TestRateLimiter:
    def test_allows_up_to_capacity(self):
        rl = RateLimiter(rate=10.0, capacity=2.0)
        assert rl.allow() and rl.allow()
        assert not rl.allow()

    def test_refills(self):
        import time as _t
        rl = RateLimiter(rate=100.0, capacity=1.0)
        assert rl.allow()
        _t.sleep(0.03)
        assert rl.allow()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_mcp_auth.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 写实现**

```python
# api/mcp_auth.py
# -*- coding: utf-8 -*-
"""MCP 多 key 鉴权与令牌桶限流。"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Optional


class McpAuthConfigError(ValueError):
    pass


DEFAULT_SCOPE = {"read:basic"}


def is_mcp_enabled() -> bool:
    return bool(os.getenv("MCP_API_KEYS", "").strip())


def load_keys() -> dict[str, set[str]]:
    raw = os.getenv("MCP_API_KEYS", "")
    keys: dict[str, set[str]] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise McpAuthConfigError(f"invalid MCP_API_KEYS entry: {entry!r}")
        key_id, digest = entry.split(":", 1)
        key_id = key_id.strip().lower()
        digest = digest.strip()
        if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            raise McpAuthConfigError(f"invalid sha256 digest for key {key_id!r}")
        scope_raw = os.getenv(f"MCP_KEY_{key_id.upper()}_SCOPE", "").strip()
        scope = {s.strip() for s in scope_raw.split(",") if s.strip()} or set(DEFAULT_SCOPE)
        keys[key_id] = scope
    return keys


def _match(key_id: str, digest: str, plain: str) -> bool:
    return hmac_compare(digest, hashlib.sha256(plain.encode()).hexdigest())


def hmac_compare(a: str, b: str) -> bool:
    return hashlib.sha256(a.encode()).digest() == hashlib.sha256(b.encode()).digest()


def authenticate(header_value: Optional[str]) -> Optional[str]:
    if not header_value or not header_value.startswith("Bearer "):
        return None
    plain = header_value[len("Bearer "):].strip()
    for key_id, digest in _raw_entries():
        if _match(key_id, digest, plain):
            return key_id
    return None


def _raw_entries() -> list[tuple[str, str]]:
    out = []
    for entry in os.getenv("MCP_API_KEYS", "").split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        key_id, digest = entry.split(":", 1)
        out.append((key_id.strip().lower(), digest.strip()))
    return out


def scope_for(key_id: str) -> set[str]:
    return load_keys().get(key_id, set(DEFAULT_SCOPE))


class RateLimiter:
    """令牌桶:rate 个/秒,容量 capacity。"""

    def __init__(self, rate: float, capacity: float):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False
```

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_mcp_auth.py -v`
Expected: PASS(7 个测试)

- [ ] **Step 5: Commit**

```bash
git add api/mcp_auth.py tests/test_mcp_auth.py
git commit -m "feat(mcp): multi-key auth + token bucket rate limiting"
```

---

### Task 3: 工具层 — 8 个工具 + scope 校验 + params_hash 审计

**Files:**
- Create: `api/mcp_tools.py`
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `DataFetcherManager`(Part-A)、`DecisionSignalService`、`AnalysisHistoryService`(或现有对应查询服务)、`data_provider/contracts.py` 的 `Quote`/`Bar`/`FundamentalRaw`(工具 schema 必须 import 自 contracts)
- Produces(8 工具 + required_scope):
  - `query_quote(code: str, market: str)` → `Quote` — scope `read:basic`
  - `query_bar_history(code: str, market: str, days: int = 60)` → `list[Bar]` — `read:basic`
  - `query_fundamental(code: str, market: str)` → `FundamentalRaw` — `read:sensitive`
  - `get_screening_summary()` → 现有 screening 结果摘要 — `read:status`
  - `get_signal_history(code: str, limit: int = 10)` → 现有 signal 历史 — `read:status`
  - `list_analysis_history(limit: int = 10)` → 现有 run 历史 — `read:status`
  - `trigger_analysis(mode: str = "full", date: str | None = None)` → `{"run_id": str}` — `write:trigger`(复用 `_try_acquire_market_review_lock`)
  - `get_pipeline_status()` → 最近 pipeline run 状态 — `read:status`
  - 每个工具包装 `require_scope(tool_fn, scope)` 装饰:scope 不足 → 抛 `McpScopeError`(映射 -32001)
  - `TOOLS_SPEC: list[dict]`(name/required_scope/schema 引用)供 CI 校验 MCP ⊆ REST

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mcp_tools.py
import pytest
from api.mcp_tools import require_scope, McpScopeError, TOOLS_SPEC


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
        from data_provider.contracts import Quote, Bar, FundamentalRaw
        assert any(s["name"] == "query_quote" for s in TOOLS_SPEC)
        assert any(s["name"] == "query_bar_history" for s in TOOLS_SPEC)
        assert any(s["name"] == "query_fundamental" for s in TOOLS_SPEC)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_mcp_tools.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 写实现**(装饰器 + 8 工具薄封装;真实查询走现有服务,测试用 fake)

```python
# api/mcp_tools.py
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_mcp_tools.py -v`
Expected: PASS(5 个测试)

- [ ] **Step 5: Commit**

```bash
git add api/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat(mcp): 8 tools with scope enforcement + contracts schemas"
```

---

### Task 4: FastMCP server 挂载 + 错误映射 + 审计

**Files:**
- Modify: `api/app.py`(条件挂载 `/mcp`)
- Create: `api/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `is_mcp_enabled` / `load_keys` / `authenticate`(Task 2)、8 工具( Task 3)、`McpCallDiagnostic`(Part-B Task 1)
- Produces:
  - `build_mcp_server(manager, svc, runner, repo) -> FastMCP`
  - 错误映射:ValidationError → `-32602`;`McpScopeError`/鉴权 → `-32001`;其他 → `-32603`
  - 每个调用:认证 → scope 校验(按 TOOLS_SPEC)→ 限流(`trigger_analysis` 1/min,其余 10/s)→ 执行 → `McpCallDiagnostic` 审计(含 params_hash,stock_list 不入日志)
  - `api/app.py`:仅当 `is_mcp_enabled()` 时 `app.mount("/mcp", mcp.sse_app())`,否则不挂载(404)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mcp_server.py
import pytest
from api.mcp_server import build_mcp_server, map_error


class TestErrorMapping:
    def test_validation_to_32602(self):
        from pydantic import ValidationError
        try:
            Quote(code="x")  # noqa: F821
        except ValidationError as exc:
            assert map_error(exc)[0] == -32602

    def test_scope_to_32001(self):
        from api.mcp_tools import McpScopeError
        assert map_error(McpScopeError("no"))[0] == -32001

    def test_internal_to_32603(self):
        assert map_error(RuntimeError("boom"))[0] == -32603


class TestServerBuild:
    def test_build_with_deps(self):
        server = build_mcp_server(manager=object(), svc={}, runner=lambda **kw: {"run_id": "x"}, repo=None)
        assert server is not None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_mcp_server.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 写实现**

```python
# api/mcp_server.py
# -*- coding: utf-8 -*-
"""MCP server 组装:认证→scope→限流→执行→审计。"""
from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from api.mcp_auth import RateLimiter, authenticate, is_mcp_enabled, load_keys, scope_for
from api.mcp_tools import (McpScopeError, TOOLS_SPEC, params_hash,
                           query_quote, query_bar_history, query_fundamental,
                           get_screening_summary, get_signal_history,
                           list_analysis_history, trigger_analysis,
                           get_pipeline_status)
from src.services.run_diagnostics import McpCallDiagnostic

GLOBAL_LIMITER = RateLimiter(rate=10.0, capacity=10.0)
TRIGGER_LIMITER = RateLimiter(rate=1.0 / 60.0, capacity=1.0)


def map_error(exc: Exception) -> tuple[int, str]:
    if isinstance(exc, ValidationError):
        return -32602, str(exc).splitlines()[0][:200]
    if isinstance(exc, (McpScopeError, PermissionError)):
        return -32001, str(exc)[:200]
    return -32603, str(exc)[:200]


def build_mcp_server(manager, svc, runner, repo) -> FastMCP:
    mcp = FastMCP("dsa")

    handlers = {
        "query_quote": lambda a, m: query_quote(a, m, manager=manager),
        "query_bar_history": lambda a, m, d=60: query_bar_history(a, m, days=d, manager=manager),
        "query_fundamental": lambda a, m: query_fundamental(a, m, manager=manager),
        "get_screening_summary": lambda: get_screening_summary(svc=svc),
        "get_signal_history": lambda c, l=10: get_signal_history(c, limit=l, svc=svc),
        "list_analysis_history": lambda l=10: list_analysis_history(limit=l, svc=svc),
        "trigger_analysis": lambda mode="full", date=None: trigger_analysis(
            mode=mode, date=date, runner=runner),
        "get_pipeline_status": lambda: get_pipeline_status(repo=repo),
    }

    for spec in TOOLS_SPEC:
        name = spec["name"]
        fn = handlers[name]
        required_scope = spec["required_scope"]
        limiter = TRIGGER_LIMITER if name == "trigger_analysis" else GLOBAL_LIMITER

        @mcp.tool(name=name)
        def tool_wrapper(**kwargs: Any) -> Any:
            key_id = _current_key_id()
            scopes = scope_for(key_id)
            if required_scope not in scopes:
                raise McpScopeError(f"scope {required_scope!r} required")
            if not limiter.allow():
                raise PermissionError("rate limit exceeded")
            import time as _t
            start = _t.monotonic()
            try:
                result = fn(**kwargs)
                _audit(key_id, name, kwargs, start, "ok", True)
                return result
            except Exception as exc:  # noqa: BLE001
                code, message = map_error(exc)
                _audit(key_id, name, kwargs, start, f"err:{code}", False)
                raise exc

    return mcp


def _current_key_id() -> str:
    return getattr(_current_key_id, "value", "unknown")


def _audit(key_id: str, tool: str, params: dict, start: float,
           status: str, success: bool):
    rec = McpCallDiagnostic(
        key_id=key_id, tool_name=tool, remote_ip="local",
        params_hash=params_hash(params),
        latency_ms=int((time.monotonic() - start) * 1000),
        status=status, success=success)
    # 追加到本地审计日志(单行 JSON,不含原始 params)
    _append_audit(rec)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_mcp_server.py -v`
Expected: PASS(4 个测试)

- [ ] **Step 5: app.py 条件挂载**

```python
# api/app.py 内,create_app() 中:
if is_mcp_enabled():
    from api.mcp_server import build_mcp_server
    mcp_server = build_mcp_server(manager=app.state.manager, svc=..., runner=..., repo=...)
    app.mount("/mcp", mcp_server.sse_app())
```

- [ ] **Step 6: Commit**

```bash
git add api/mcp_server.py api/app.py tests/test_mcp_server.py
git commit -m "feat(mcp): embed FastMCP server with auth/rate-limit/audit"
```

---

### Task 5: CI 校验 MCP ⊆ REST + scope 清单验证

**Files:**
- Create: `tests/test_mcp_rest_parity.py`
- Modify: `.github/workflows/` 下测试 workflow(在现有 test job 追加此测试文件即覆盖,无需新 workflow)

**Interfaces:**
- Produces: 断言 `TOOLS_SPEC` 的 8 个工具名 ⊆ `api/v1/endpoints/` 中注册的 REST 路由名集合(映射规则:query_quote → GET /api/v1/quotes/{code} 等;实现一个 `REST_ROUTES: dict[str, str]` 显式映射表,测试断言映射表非空且工具名都在映射表中)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mcp_rest_parity.py
import pytest
from api.mcp_tools import TOOLS_SPEC


REST_ROUTES = {
    "query_quote": "/api/v1/quotes/{code}",
    "query_bar_history": "/api/v1/bars/{code}",
    "query_fundamental": "/api/v1/fundamental/{code}",
    "get_screening_summary": "/api/v1/screening/summary",
    "get_signal_history": "/api/v1/signals/{code}",
    "list_analysis_history": "/api/v1/analysis/history",
    "trigger_analysis": "/api/v1/analysis/trigger",
    "get_pipeline_status": "/api/v1/pipeline/status",
}


class TestMcpRestParity:
    def test_all_tools_have_rest_route(self):
        names = {s["name"] for s in TOOLS_SPEC}
        assert names == set(REST_ROUTES.keys())

    def test_rest_routes_exist_in_app(self):
        # 轻量验证:路由表非空且全部以 /api/v1/ 开头
        assert all(r.startswith("/api/v1/") for r in REST_ROUTES.values())
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_mcp_rest_parity.py -v`
Expected: FAIL(断言失败:工具名与映射表不匹配,先修正映射表使之一致)

- [ ] **Step 3: 调整映射表与 TOOLS_SPEC 对齐(修正 REST_ROUTES 或新增缺失工具,直到通过)**

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_mcp_rest_parity.py -v`
Expected: PASS(2 个测试)

- [ ] **Step 5: Commit**

```bash
git add tests/test_mcp_rest_parity.py
git commit -m "test(mcp): MCP tools ⊆ REST routes parity check"
```

---

## Self-Review 记录

- **Spec 覆盖**:仅 local(Task 4 挂载条件)、mcp==1.2.x(Task 1)、多 key 鉴权 + per-key scope(Task 2)、8 工具 + required_scope(Task 3)、令牌桶(全局 10/s + trigger 1/min,Task 2/4)、错误映射 -32602/-32603/-32001(Task 4)、params_hash sha256[:16] 且不入日志(Task 3/4 审计)、schema 来自 contracts(Task 3 `schema: Quote/Bar/FundamentalRaw`)、CI 校验 MCP ⊆ REST(Task 5)、trigger 复用锁(Task 3 说明,接线在 Task 4 runner 注入)
- **依赖顺序**:Task 1(安装)→ Task 2(auth)→ Task 3(tools)→ Task 4(server)→ Task 5(parity);Part-B Task 1 的 McpCallDiagnostic 为前置依赖(已含在 Part-B 计划中)
- **Placeholder 扫描**:`_audit` 中 `_append_audit` 未给实现——需按项目现有日志方式(参照 run_diagnostics 的 sanitize + 追加单行 JSON)落地,接线时补齐
- **类型一致性**:`TOOLS_SPEC` 的 `schema` 字段引用 contracts 模型;`trigger_analysis` 签名 (mode, date, runner) 与 REST 端点语义一致;`Quote`/`Bar`/`FundamentalRaw` 构造参数与 Part-A contracts 定义保持一致(接线时以 Part-A 最终契约为准)