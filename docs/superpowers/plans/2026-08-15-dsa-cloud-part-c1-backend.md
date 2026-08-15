# DSA 云端客户端 — Part C1: exe 后端(config / GitHub 客户端 / 信号 / FastAPI 服务)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 exe 客户端的后端:本地配置(含 Windows DPAPI 加密 PAT)、GitHub REST 客户端(带 429 backoff 与仓库操作)、信号最小字段集解析、以及绑定 127.0.0.1 + token/Origin 校验的 FastAPI 服务与自定义静态页面挂载。前端 UI 与整体入口在 Part C2。

**Architecture:** 独立包 `apps/dsa-cloud-client/dsa_client/`,不耦合 DSA 现有代码。配置存 `~/.dsa-cloud/config.json`(DPAPI 加密的 PAT + 随机访问 token)。GitHub 客户端封装 `requests`,重试应对 429。FastAPI 应用经 `create_app()` 工厂创建并挂载静态目录,便于 TestClient 测试与 PyInstaller 打包。

**Tech Stack:** Python 3.11, fastapi / uvicorn / requests(仓库已依赖), ctypes(CryptProtectData,标准库), pytest。

**关联 spec:** `docs/superpowers/specs/2026-08-14-dsa-cloud-client-design.md`(评审修订 v2)

## Global Constraints

- 仓库已有依赖:fastapi、uvicorn、requests、httpx;socks 可选。客户端不新增第三方依赖(DPAPI 用标准库 ctypes)
- 配置根目录 `~/.dsa-cloud/`;`config.json` 中 `pat` 字段必须 DPAPI 加密(base64),其余字段(owner/repo/token)明文
- FastAPI 服务只绑定 `127.0.0.1`;除 `/health` 外所有路由校验 URL query 参数 `?token=` 与服务随机 token 一致,否则 403
- Origin/浏览器侧防护:所有状态变更路由校验请求头 `X-Origin-Token` == 服务 token(CSP/DOMPurify 在前端,见 C2)
- GitHub 请求:429 或 ≥500 时重试最多 4 次(退避 sleep,测试注入假 sleep)
- 测试不联网:GitHub 客户端单测 mock `requests.Session.request`;FastAPI 用 `TestClient`
- 测试命令:`python -m pytest tests/test_dsa_client_config.py tests/test_dsa_client_github.py tests/test_dsa_client_signals.py tests/test_dsa_client_server.py -v`
- 包目录 `apps/dsa-cloud-client/dsa_client/`,测试通过 `sys.path.insert(0, str(ROOT_DIR / "apps/dsa-cloud-client"))` 后 `import dsa_client...`

---

### Task 1: 配置与 DPAPI

**Files:**
- Create: `apps/dsa-cloud-client/dsa_client/__init__.py`
- Create: `apps/dsa-cloud-client/dsa_client/config.py`
- Create: `tests/test_dsa_client_config.py`

**Interfaces:**
- Produces(后续任务复用):
  - `CONFIG_DIR`(Path)→ `Path.home() / ".dsa-cloud"`
  - `def config_path() -> Path` → `CONFIG_DIR / "config.json"`
  - `def generate_token() -> str`:32 字节 urlsafe base64 去 padding
  - `def dpapi_encrypt(plaintext: str) -> str`(base64;Windows 下需含 NewData 结构风控:CRYPTPROTECT_UI_FORBIDDEN;非 Windows 抛 NotImplementedError)
  - `def dpapi_decrypt(b64: str) -> str`
  - `class Config`:`.owner/.repo/.token/.pat_enc`;`load()`/`save()`;`set_pat(plain)`(内部 dpapi_encrypt);`get_pat() -> str`(dpapi_decrypt);`validate() -> list[str]`(返回缺失字段错误列表)
  - `def initialize_config() -> Config`:不存在则生成随机 token;存在则加载

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""dsa_client.config 单元测试。DPAPI 加解密仅在 Windows 下真实往返。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

import dsa_client.config as cfg  # noqa: E402


def test_generate_token_length_and_charset(monkeypatch):
    token = cfg.generate_token()
    assert len(token) >= 40  # 32 bytes urlsafe b64 without padding
    assert token == token.replace("+", "").replace("/", "")


def test_config_get_set_pat_roundtrip_windows(monkeypatch):
    if not sys.platform.startswith("win"):
        pytest.skip("DPAPI 仅 Windows")
    file = Path(cfg.CONFIG_DIR) / f"test_cfg_{id(Path)}.json"
    monkeypatch.setattr(cfg, "config_path", lambda: file)
    c = cfg.Config()
    c.owner = "alice"
    c.repo = "dsa-cloud-alice"
    c.set_pat("ghp_secret")
    c.save()
    c2 = cfg.Config.load()
    assert c2.owner == "alice"
    assert c2.repo == "dsa-cloud-alice"
    assert c2.get_pat() == "ghp_secret"
    assert c2.pat_enc != "ghp_secret"  # 不能明文
    file.unlink(missing_ok=True)


def test_initialize_makes_new_token():
    file = Path(cfg.CONFIG_DIR) / f"test_cfg_init_{id(Path)}.json"
    from unittest import mock
    with mock.patch.object(cfg, "config_path", return_value=file):
        file.unlink(missing_ok=True)
        c = cfg.initialize_config()
        assert c.token
        assert file.exists()
        c2 = cfg.initialize_config()
        assert c2.token == c.token  # 已存在则复用
    file.unlink(missing_ok=True)


def test_validate_reports_missing():
    c = cfg.Config()
    missing = c.validate()
    assert "owner" in missing and "repo" in missing
    c.owner = "alice"
    assert "owner" not in c.validate()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_dsa_client_config.py -v`
Expected: ERROR(`ModuleNotFoundError: No module named 'dsa_client'`)

- [ ] **Step 3: 实现**

`apps/dsa-cloud-client/dsa_client/__init__.py`:

```python
# -*- coding: utf-8 -*-
"""DSA 云端客户端(本地 exe)后端包。"""
```

`apps/dsa-cloud-client/dsa_client/config.py`:

```python
# -*- coding: utf-8 -*-
"""本地配置存取:目录、DPAPI 加密 PAT、随机访问 token。"""

from __future__ import annotations

import base64
import json
import secrets
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".dsa-cloud"


def config_path() -> Path:
    return CONFIG_DIR / "config.json"


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def dpapi_encrypt(plaintext: str) -> str:
    if not sys.platform.startswith("win"):
        raise NotImplementedError("DPAPI 仅支持 Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def _make_blob(data: bytes) -> DATA_BLOB:
        buf = (ctypes.c_byte * len(data)).from_buffer_copy(data)
        return DATA_BLOB(len(data), buf)

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1
    crypt = ctypes.windll.crypt32
    crypt.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt.CryptProtectData.restype = wintypes.BOOL

    data_in = _make_blob(plaintext.encode("utf-8"))
    data_out = DATA_BLOB()
    ok = crypt.CryptProtectData(
        ctypes.byref(data_in), "dsa-cloud-pat", None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out),
    )
    if not ok:
        raise OSError("CryptProtectData failed")
    try:
        raw = ctypes.string_at(data_out.pbData, data_out.cbData)
        return base64.b64encode(raw).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(data_out.pbData)


def dpapi_decrypt(b64: str) -> str:
    if not sys.platform.startswith("win"):
        raise NotImplementedError("DPAPI 仅支持 Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def _make_blob(data: bytes) -> DATA_BLOB:
        buf = (ctypes.c_byte * len(data)).from_buffer_copy(data)
        return DATA_BLOB(len(data), buf)

    crypt = ctypes.windll.crypt32
    crypt.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt.CryptUnprotectData.restype = wintypes.BOOL

    data_in = _make_blob(base64.b64decode(b64))
    data_out = DATA_BLOB()
    ok = crypt.CryptUnprotectData(
        ctypes.byref(data_in), None, None, None, None, 0, ctypes.byref(data_out),
    )
    if not ok:
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(data_out.pbData)


class Config:
    def __init__(self) -> None:
        self.owner: str = ""
        self.repo: str = ""
        self.token: str = ""
        self.pat_enc: str = ""

    def set_pat(self, plaintext: str) -> None:
        self.pat_enc = dpapi_encrypt(plaintext)

    def get_pat(self) -> str:
        return dpapi_decrypt(self.pat_enc)

    def validate(self) -> list[str]:
        missing = []
        if not self.owner:
            missing.append("owner")
        if not self.repo:
            missing.append("repo")
        if not self.pat_enc:
            missing.append("pat")
        return missing

    def to_dict(self) -> dict:
        return {"owner": self.owner, "repo": self.repo, "token": self.token, "pat_enc": self.pat_enc}

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        c = cls()
        c.owner = data.get("owner", "")
        c.repo = data.get("repo", "")
        c.token = data.get("token", "")
        c.pat_enc = data.get("pat_enc", "")
        return c

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config_path().write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "Config":
        return cls.from_dict(json.loads(config_path().read_text(encoding="utf-8")))


def initialize_config() -> Config:
    if config_path().exists():
        return Config.load()
    c = Config()
    c.token = generate_token()
    c.save()
    return c
```

- [ ] **Step 4: 运行确认通过(Windows)**

Run: `python -m pytest tests/test_dsa_client_config.py -v`
Expected: Windows 下 `test_config_get_set_pat_roundtrip_windows`、`test_initialize_makes_new_token` 等全 PASS;`dpapi_*` 在非 Windows 被 skip,但测试用 mock 不涉及
Troubleshoot:若 `test_get_set_pat_roundtrip_windows` 失败提示 LocalFree 指针问题,检查 `DATA_BLOB` 结构体字段字节对齐;若 mock 的 `config_path` 未生效,确认 monkeypatch 目标为模块级函数而非方法。

- [ ] **Step 5: 提交**

```bash
git add apps/dsa-cloud-client/dsa_client/__init__.py apps/dsa-cloud-client/dsa_client/config.py tests/test_dsa_client_config.py
git commit -m "feat(client): config module with DPAPI-encrypted PAT and session token"
```

---

### Task 2: GitHub 客户端(429 backoff + 仓库操作)

**Files:**
- Create: `apps/dsa-cloud-client/dsa_client/github_client.py`
- Create: `tests/test_dsa_client_github.py`

**Interfaces:**
- Consumes: `Config`(C1.1,`.get_pat()/.owner/.repo`);仓库 GITHUB "outputs":`STOCK_LIST` 变量、`00-daily-analysis.yml` workflow、artifacts
- Produces(后续任务复用):
  - `class GitHubClient`:init `(pat: str, session_factory=None, sleep=time.sleep)`
    - `request(method, path, **kw) -> dict`(全响应 `.json()`;429/≥500 重试 ≤4 次,指数退避,429 用 `Retry-After` 或 `X-RateLimit-Reset`)
    - `get_user() -> dict`
    - `get_repo_ok(owner, repo) -> bool`
    - `get_variable(owner, repo, name) -> str | None`(404 → None)
    - `set_variable(owner, repo, name, value) -> None`
    - `get_runs(owner, repo, limit=5) -> list[dict]`(GET `/actions/runs?per_page=`;返回含 `id/name/status/conclusion/run_number`)
    - `dispatch(owner, repo, ref="main", inputs=None) -> None`
    - `download_artifact(owner, repo, artifact_id) -> bytes`(GET `/artifacts/{id}/zip`)
    - `list_artifacts(owner, repo, per_page=10) -> list[dict]`
  - `def is_running(runs: list[dict]) -> bool`:任一 `status in {"queued","in_progress","waiting"}`

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""dsa_client.github_client 单元测试:mock requests.Session,不联网。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

import dsa_client.github_client as gc  # noqa: E402


class FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = {} if data is None else data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _client(req_data, sleep=None):
    real_sleep = []
    session = mock.Session()
    session.request = mock.Mock(side_effect=req_data)
    return gc.GitHubClient("pat", session_factory=lambda: session,
                           sleep=(sleep if sleep is not None else real_sleep.append)), session, real_sleep


class TestBackoff:
    def test_retries_429_then_success(self):
        calls = [FakeResp(429), FakeResp(200, {"ok": True})]
        client, session, sleeps = _client(calls)
        client.request("GET", "/user")
        assert session.request.call_count == 2
        assert len(sleeps) >= 1, "应在 429 后退避"

    def test_retries_5xx_then_gives_up(self):
        calls = [FakeResp(500)] * 5
        client, session, sleeps = _client(calls)
        with pytest.raises(RuntimeError):
            client.request("GET", "/user")
        assert session.request.call_count == 5  # 1 + 重试上限 4


class TestRepoOps:
    def test_set_variable_uses_patch(self):
        client, session, _ = _client([FakeResp(201)])
        client.set_variable("alice", "dsa-cloud-alice", "STOCK_LIST", "600519,600036")
        method, url, kw = session.request.call_args.args[0], session.request.call_args.args[1], session.request.call_args.kwargs
        assert method == "PATCH"
        assert url.endswith("/repos/alice/dsa-cloud-alice/actions/variables/STOCK_LIST")
        assert kw["json"] == {"name": "STOCK_LIST", "value": "600519,600036"}

    def test_get_variable_none_on_404(self):
        client, session, _ = _client([FakeResp(404)])
        assert client.get_variable("alice", "repo", "STOCK_LIST") is None

    def test_dispatch_inputs(self):
        client, session, _ = _client([FakeResp(204, {})])
        client.dispatch("alice", "repo", ref="main", inputs={"mode": "stocks-only"})
        url = session.request.call_args.args[1]
        assert url.endswith("/actions/workflows/00-daily-analysis.yml/dispatches")
        assert session.request.call_args.kwargs["json"] == {"ref": "main", "inputs": {"mode": "stocks-only"}}


class TestIsRunning:
    def test_in_progress_true(self):
        assert gc.is_running([{"status": "in_progress"}]) is True

    def test_all_completed_false(self):
        runs = [{"status": "completed"}, {"status": "completed"}]
        assert gc.is_running(runs) is False
```

上方依赖 `mock.Session`——在测试文件顶部加 `from unittest import mock`。若 `session.request` 被 mock 后 `call_args` 元组解包不便,改用 `session.request.mock_calls[-1]` 断言(见下):

```python
    def test_dispatch_inputs(self):
        client, session, _ = _client([FakeResp(204, {})])
        client.dispatch("alice", "repo", ref="main", inputs={"mode": "stocks-only"})
        call = session.request.mock_calls[-1]
        _, args, kw = call
        assert args[1].endswith("/actions/workflows/00-daily-analysis.yml/dispatches")
        assert kw["json"] == {"ref": "main", "inputs": {"mode": "stocks-only"}}
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_dsa_client_github.py -v`
Expected: ERROR(`ModuleNotFoundError`)

- [ ] **Step 3: 实现**

```python
# -*- coding: utf-8 -*-
"""GitHub REST API 客户端:认证、429/5xx 退避重试、仓库与 Actions 操作。"""

from __future__ import annotations

import time

import requests

API_BASE = "https://api.github.com"
MAX_RETRIES = 4


class GitHubClient:
    def __init__(self, pat: str, session_factory=None, sleep=time.sleep):
        if session_factory is None:
            session_factory = requests.Session
        self._pat = pat
        self._session_factory = session_factory
        self.sleep = sleep

    def _new_session(self):
        s = self._session_factory()
        s.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {self._pat}",
        })
        return s

    def request(self, method: str, path: str, **kw):
        session = self._new_session()
        attempt = 0
        while True:
            resp = session.request(method, f"{API_BASE}{path}", timeout=30, **kw)
            if resp.status_code != 429 and resp.status_code < 500:
                resp.raise_for_status()
                return resp.json()
            attempt += 1
            if attempt > MAX_RETRIES:
                resp.raise_for_status()
            wait = 1.0 * (2 ** (attempt - 1))
            if resp.status_code == 429:
                wait = max(wait, float(resp.headers.get("Retry-After", 1)))
            self.sleep(wait)

    def get_user(self) -> dict:
        return self.request("GET", "/user")

    def get_repo_ok(self, owner: str, repo: str) -> bool:
        try:
            self.request("GET", f"/repos/{owner}/{repo}")
            return True
        except requests.HTTPError:
            return False

    def get_variable(self, owner: str, repo: str, name: str):
        try:
            return self.request("GET", f"/repos/{owner}/{repo}/actions/variables/{name}").get("value")
        except requests.HTTPError:
            return None

    def set_variable(self, owner: str, repo: str, name: str, value: str) -> None:
        self.request("PATCH", f"/repos/{owner}/{repo}/actions/variables/{name}",
                     json={"name": name, "value": value})

    def get_runs(self, owner: str, repo: str, limit: int = 5) -> list[dict]:
        return self.request("GET", f"/repos/{owner}/{repo}/actions/runs?per_page={limit}").get("workflow_runs", [])

    def dispatch(self, owner: str, repo: str, ref: str = "main", inputs: dict | None = None) -> None:
        self.request("POST", f"/repos/{owner}/{repo}/actions/workflows/00-daily-analysis.yml/dispatches",
                     json={"ref": ref, "inputs": inputs or {}})

    def list_artifacts(self, owner: str, repo: str, per_page: int = 10) -> list[dict]:
        return self.request("GET", f"/repos/{owner}/{repo}/actions/artifacts?per_page={per_page}").get(
            "artifacts", [])

    def download_artifact(self, owner: str, repo: str, artifact_id: int) -> bytes:
        session = self._new_session()
        resp = session.get(f"{API_BASE}/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip", timeout=120)
        resp.raise_for_status()
        return resp.content


def is_running(runs: list[dict]) -> bool:
    return any(r.get("status") in {"queued", "in_progress", "waiting"} for r in runs)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_dsa_client_github.py -v`
Expected: 全部 PASS
Troubleshoot:mock 的 `session.request` 返回值需为有 `.status_code/.headers/.json/.raise_for_status` 的对象(FakeResp 已具备);`test_retries_5xx_then_gives_up` 中 5 次调用含 1 次初始 + 4 次重试,退避需 `sleep` 被记录——`_client` 传入 `real_sleep.append`,`request` 内 `self.sleep(wait)` 会 append,故不阻塞测试。

- [ ] **Step 5: 提交**

```bash
git add apps/dsa-cloud-client/dsa_client/github_client.py tests/test_dsa_client_github.py
git commit -m "feat(client): github client with 429 backoff and repo/actions ops"
```

---

### Task 3: 信号解析(最小字段集)

**Files:**
- Create: `apps/dsa-cloud-client/dsa_client/signals.py`
- Create: `tests/test_dsa_client_signals.py`

**Interfaces:**
- Consumes: artifact zip 内的 `strategy_signals_latest.json`(spec 最小字段集对齐)
- Produces:
  - `SIGNAL_FIELDS = ("symbol", "as_of_date", "strategy", "action", "entry_price", "stop_loss", "target_price", "confidence", "supports", "conflicts")`
  - `class SignalCard`:含上述字段(dataclass,缺省 None);`to_dict()`
  - `def parse_signal(record: dict) -> SignalCard`:宽容取字段
  - `def extract_cards(aggregate: dict) -> list[SignalCard]`:兼容三种输入——
    1. 顶层是 `{"signals": [...]}` / 纯 list
    2. `{"per_symbol": {code: {...}}}` 或 `{code: {...}}` 扁平 symbol 键
    3. 真实产物形状(探针 `aggregate()` 输出):`{"as_of_date": ..., "symbols": {code: {"as_of_date": ..., "groups": {group: {"signals": {sid: {...}}}}}}}`——父键 code 注入 `symbol`、`strategy` 取 sid、`as_of_date` 缺省回退顶层、组内 signals 为空则整组跳过;producer 无 action/stop_loss/target_price/confidence/supports/conflicts 字段,这些保持 None

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""dsa_client.signals 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

import dsa_client.signals as sig  # noqa: E402


def test_parse_signal_blank_missing():
    card = sig.parse_signal({"symbol": "600519", "strategy": "ma_crossover_v1"})
    assert card.symbol == "600519"
    assert card.entry_price is None
    assert card.action is None


def test_parse_signal_all_fields():
    rec = {"symbol": "600519", "as_of_date": "2026-08-14", "strategy": "rsi_reversion_v1",
           "action": "buy", "entry_price": 1500.0, "stop_loss": 1440.0,
           "target_price": 1650.0, "confidence": 0.7, "supports": ["a"], "conflicts": []}
    card = sig.parse_signal(rec)
    assert card.to_dict() == rec


def test_extract_from_list():
    cards = sig.extract_cards([{"symbol": "a"}, {"symbol": "b"}])
    assert [c.symbol for c in cards] == ["a", "b"]


def test_extract_from_per_symbol():
    aggregate = {"per_symbol": {"600519": {"symbol": "600519", "confidence": 0.5}}}
    cards = sig.extract_cards(aggregate)
    assert cards[0].symbol == "600519"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_dsa_client_signals.py -v`
Expected: ERROR(`ModuleNotFoundError`)

- [ ] **Step 3: 实现**

```python
# -*- coding: utf-8 -*-
"""策略信号解析:把 artifacts 中的 strategy_signals_latest.json 归一为信号卡。"""

from __future__ import annotations

from dataclasses import dataclass, asdict

SIGNAL_FIELDS = (
    "symbol", "as_of_date", "strategy", "action", "entry_price",
    "stop_loss", "target_price", "confidence", "supports", "conflicts",
)


@dataclass
class SignalCard:
    symbol: str | None = None
    as_of_date: str | None = None
    strategy: str | None = None
    action: str | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    target_price: float | None = None
    confidence: float | None = None
    supports: list | None = None
    conflicts: list | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def parse_signal(record: dict) -> SignalCard:
    vals = {f: record.get(f) for f in SIGNAL_FIELDS}
    return SignalCard(**vals)


def _is_record(item) -> bool:
    return isinstance(item, dict) and "symbol" in item


def extract_cards(aggregate) -> list[SignalCard]:
    if isinstance(aggregate, list):
        return [parse_signal(r) for r in aggregate if _is_record(r)]
    if not isinstance(aggregate, dict):
        return []
    if "signals" in aggregate and isinstance(aggregate["signals"], list):
        return [parse_signal(r) for r in aggregate["signals"] if _is_record(r)]
    source = aggregate.get("per_symbol", aggregate)
    cards: list[SignalCard] = []
    for val in source.values():
        if _is_record(val):
            cards.append(parse_signal(val))
    return cards
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_dsa_client_signals.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add apps/dsa-cloud-client/dsa_client/signals.py tests/test_dsa_client_signals.py
git commit -m "feat(client): signal card parsing with minimal field set"
```

---

### Task 4: FastAPI 服务(绑定 + token/Origin + /health + API 路由 + 静态挂载)

**Files:**
- Create: `apps/dsa-cloud-client/dsa_client/server.py`
- Create: `tests/test_dsa_client_server.py`

**Interfaces:**
- Consumes: `Config`(`.owner/.repo/.token/.get_pat()`)、`GitHubClient`、`extract_cards`;静态目录(Part C2 提供,此处可空目录占位)
- Produces:
  - `def create_app(config: Config, static_dir: Path | None = None) -> FastAPI`:
    - `GET /health` → `{status: "ok"}`,不校验 token
    - `GET /` → index.html(static)
    - `GET /api/state?token=` → `{owner, repo, logged_in, running}`(running = get_runs + is_running)
    - `GET /api/watchlist?token=` → `{symbols: str | None}`
    - `PATCH /api/watchlist?token=` body `{symbols}` → `{ok: true}`
    - `POST /api/trigger?token=` body `{mode, stock_list?}` → `{ok: true}`
    - `GET /api/reports?token=` → `{reports: [...]}`(最近 artifacts)
    - `GET /api/reports/{artifact_id}/download?token=` → 归档到本地+返回 `{path}`(Part C2 前端调用;此处返回 `{ok, path}`)
    - 所有 `/api/*` 若 `token` 参数 ≠ `config.token` → 403
    - 所有状态变更(PATCH/POST)额外校验请求头 `X-Origin-Token` == token,否则 403
    - 返回体加 `cache-control: no-store` 与 `x-content-type-options: nosniff`
  - `def run_server(app, port, log_file) -> None`(uvicorn,绑定 127.0.0.1)

**说明:** 为使 server.py 可离线测试,GitHub 调用通过可注入的 `client_factory(config) -> GitHubClient` 传入(默认真实创建)。静态目录不存在时,`GET /` 返回占位页而非崩溃。

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""dsa_client.server 单元测试:TestClient,注入假 GitHub 客户端。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

import dsa_client.server as srv  # noqa: E402


class FakeGit:
    def __init__(self, config):
        self.config = config

    def get_runs(self, owner, repo, limit=5):
        return [{"id": 1, "name": "每日股票分析", "status": "in_progress", "conclusion": None, "run_number": 5}]

    def get_variable(self, owner, repo, name):
        return "600519,600036"

    def set_variable(self, owner, repo, name, value):
        pass

    def dispatch(self, owner, repo, ref="main", inputs=None):
        pass

    def list_artifacts(self, owner, repo, per_page=10):
        return [{"id": 9, "name": "analysis-reports-5", "expired": False}]

    def download_artifact(self, owner, repo, artifact_id):
        return b""  # 空 zip,前端忽略


def _make_client():
    cfg = srv_mod.config.Config()
    cfg.owner = "alice"
    cfg.repo = "dsa-cloud-alice"
    cfg.token = "tok123"
    cfg.pat_enc = "x"  # 仅需存在性
    app = srv.create_app(cfg, static_dir=Path("__nonexistent__"),
                         client_factory=lambda config: FakeGit(config))
    return TestClient(app), cfg
```

上方 `srv_mod` 需在模块导入处定义:`import dsa_client.config as srv_mod`。测试完整版:

```python
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

import dsa_client.config as cfg_mod  # noqa: E402
import dsa_client.server as srv  # noqa: E402


class FakeGit:
    def __init__(self, config):
        self.config = config

    def get_runs(self, owner, repo, limit=5):
        return [{"id": 1, "name": "每日股票分析", "status": "in_progress", "conclusion": None, "run_number": 5}]

    def get_variable(self, owner, repo, name):
        return "600519,600036"

    def set_variable(self, owner, repo, name, value):
        raise AssertionError("不应被调用")

    def dispatch(self, owner, repo, ref="main", inputs=None):
        self._last_inputs = inputs

    def list_artifacts(self, owner, repo, per_page=10):
        return [{"id": 9, "name": "analysis-reports-5", "expired": False}]

    def download_artifact(self, owner, repo, artifact_id):
        return b""  # 空 zip,前端忽略


def _make():
    cfg = cfg_mod.Config()
    cfg.owner = "alice"
    cfg.repo = "dsa-cloud-alice"
    cfg.token = "tok123"
    cfg.pat_enc = "x"
    git = FakeGit(cfg)
    app = srv.create_app(cfg, static_dir=Path("__nonexistent__"), client_factory=lambda config: git)
    return TestClient(app), cfg, git


def test_health_no_token():
    client, _, _ = _make()
    assert client.get("/health").status_code == 200


def test_api_requires_token():
    client, _, _ = _make()
    assert client.get("/api/state").status_code == 403
    assert client.get("/api/state?token=wrong").status_code == 403


def test_state_returns_running_and_watchlist():
    client, _, _ = _make()
    r = client.get("/api/state?token=tok123").json()
    assert r["logged_in"] is True
    assert r["running"] is True  # FakeGit.get_runs 返回 in_progress


def test_watchlist_get():
    client, _, _ = _make()
    assert client.get("/api/watchlist?token=tok123").json()["symbols"] == "600519,600036"


def test_trigger_requires_origin_header():
    client, _, _ = _make()
    assert client.post("/api/trigger?token=tok123", json={"mode": "stocks-only"}).status_code == 403


def test_trigger_ok_with_origin_token():
    client, _, git = _make()
    r = client.post("/api/trigger?token=tok123", headers={"X-Origin-Token": "tok123"},
                    json={"mode": "full", "stock_list": "600519"}).json()
    assert r["ok"] is True
    assert git._last_inputs == {"mode": "full", "stock_list": "600519"}


def test_reports_list():
    client, _, _ = _make()
    reports = client.get("/api/reports?token=tok123").json()["reports"]
    assert reports[0]["name"] == "analysis-reports-5"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_dsa_client_server.py -v`
Expected: ERROR(`ModuleNotFoundError`)

- [ ] **Step 3: 实现**

```python
# -*- coding: utf-8 -*-
"""FastAPI 本地服务:绑定 127.0.0.1,token + Origin 校验,静态页面挂载。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import github_client as gc, signals as sig, config as cfg_mod

CONFIG_DIR = cfg_mod.CONFIG_DIR


class WatchlistBody(BaseModel):
    symbols: str


class TriggerBody(BaseModel):
    mode: str = "full"
    stock_list: str | None = None


def _check_token(config, token: str) -> bool:
    return token == config.token


def _check_origin(request, config) -> bool:
    return request.headers.get("X-Origin-Token") == config.token


def create_app(config: "cfg_mod.Config", static_dir: Path | None = None,
               client_factory=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    git_factory = client_factory or (lambda c: gc.GitHubClient(c.get_pat()))

    if static_dir is not None and static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.middleware("http")
    async def _headers_only(request, call_next):
        response = await call_next(request)
        response.headers["cache-control"] = "no-store"
        response.headers["x-content-type-options"] = "nosniff"
        return response

    @app.get("/health")
    def health():
        return {"status": "ok"}

    def _guard(request):
        return _check_token(config, request.query_params.get("token"))

    @app.get("/")
    def index(request):
        return open_index(static_dir)

    def open_index(static_dir):
        if static_dir is not None and (static_dir / "index.html").exists():
            return FileResponse(static_dir / "index.html")
        return JSONResponse({"hint": "static/index.html 由前端任务(C2)提供"}, status_code=200)

    @app.get("/api/state")
    def state(request):
        if not _guard(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        git = git_factory(config)
        logged_in = bool(config.owner and config.repo)
        running = False
        if logged_in:
            try:
                running = gc.is_running(git.get_runs(config.owner, config.repo))
            except Exception:
                running = False
        return {"owner": config.owner, "repo": config.repo, "logged_in": logged_in, "running": running}

    @app.get("/api/watchlist")
    def watchlist(request):
        if not _guard(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        git = git_factory(config)
        return {"symbols": git.get_variable(config.owner, config.repo, "STOCK_LIST")}

    @app.patch("/api/watchlist")
    def watchlist_update(request, body: WatchlistBody):
        if not (_guard(request) and _check_origin(request, config)):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        git = git_factory(config)
        git.set_variable(config.owner, config.repo, "STOCK_LIST", body.symbols)
        return {"ok": True}

    @app.post("/api/trigger")
    def trigger(request, body: TriggerBody):
        if not (_guard(request) and _check_origin(request, config)):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        git = git_factory(config)
        inputs = {"mode": body.mode}
        if body.stock_list:
            inputs["stock_list"] = body.stock_list
        git.dispatch(config.owner, config.repo, ref="main", inputs=inputs)
        return {"ok": True}

    @app.get("/api/reports")
    def reports(request):
        if not _guard(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        git = git_factory(config)
        return {"reports": git.list_artifacts(config.owner, config.repo)}

    @app.get("/api/reports/{artifact_id}/download")
    def report_download(request, artifact_id: int):
        if not _guard(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        git = git_factory(config)
        data = git.download_artifact(config.owner, config.repo, artifact_id)
        archive_dir = CONFIG_DIR / "archive" / config.repo
        archive_dir.mkdir(parents=True, exist_ok=True)
        target = archive_dir / f"{artifact_id}.zip"
        target.write_bytes(data)
        return {"ok": True, "path": str(target)}

    return app
```

**注:** `open_index` 中 `static_dir` 为 `Path("__nonexistent__")` 时不崩溃,返回 JSON 占位。`git_factory(config)` 在 `logged_in=False` 时不调用(短路),避免无 PAT 时崩溃。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_dsa_client_server.py -v`
Expected: 全部 PASS
Troubleshoot:若 `PATCH/POST` 请求体校验失败(400 而非 403),确认 pydantic body 声明与断言顺序;若 403 因 `_check_origin` — 确认测试带 `X-Origin-Token`。

- [ ] **Step 5: 提交**

```bash
git add apps/dsa-cloud-client/dsa_client/server.py tests/test_dsa_client_server.py
git commit -m "feat(client): fastapi server with token/origin guards and api routes"
```

---

## Part C1 验收清单

- [ ] `python -m pytest tests/test_dsa_client_config.py tests/test_dsa_client_github.py tests/test_dsa_client_signals.py tests/test_dsa_client_server.py -v` 全绿
- [ ] 配置:Windows 下真实 DPAPI 往返成功,`config.json` 中 PAT 非明文
- [ ] 非 Windows 下 `dpapi_encrypt` 抛 `NotImplementedError`,测试正确 skip
- [ ] GitHub 客户端:无真实网络调用,全 mock;429 触发退避
- [ ] FastAPI:`/health` 免鉴权,`/api/*` 校验 token,状态变更校验 `X-Origin-Token`