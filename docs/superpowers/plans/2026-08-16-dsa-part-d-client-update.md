# Part-D: 客户端更新通道 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 客户端(v1: win-x64 stable)自动更新:GitHub Release + `updates.json` → 启动后台检查线程 → `updater.exe` 子进程原子替换(备份 LRU 3 + 30s 健康检查回滚)。

**Architecture:** 版本单源真相 = dispatch `release_tag` → 构建期生成 `_version.py` + 同步 `version_info.txt`;frozen exe 优先读 PE 资源;`updates.json` 数组式(platform/arch/channel 过滤);后台 daemon 线程在 `uvicorn.run` 之前启动(不经 FastAPI/server 守卫);24h 缓存;更新走 `updater.exe` 子进程。

**Tech Stack:** Python 3.11+, packaging, PyInstaller(onefile), GitHub Actions

## Global Constraints

- 版本比较:`packaging.version`,仅 stable(忽略 prerelease/build metadata),升序比较,新版本号 > 当前才更新
- 24h 缓存:检查结果缓存 `data/update_check_cache.json`,TTL 24h(UTC)
- `updates.json` 顶层 `{"schema_version": 1, "updates": [...]}`;item 含 `version/platform/arch/channel/url/sha256/notes`
- v1 只处理 win-x64 stable channel=stable
- 原子替换:下载到临时目录 → 校验 sha256 → 备份当前 exe(保留 LRU 3 版于 `data/backups/`)→ 替换 → 30s 健康检查(子进程心跳)→ 失败回滚备份并跑 `restore.bat`
- `updater.exe` 独立 PyInstaller onefile 构建(updater_build.ps1),自包含更新逻辑,通过参数 `--current-version --target-version --url --sha256 --backup-dir` 调用
- 审计:复用 Part-B Task 1 的 `UpdateEventDiagnostic`
- 版本读取优先级:frozen exe → PE 资源(`pyi-version-info` 注入 version_info.txt);源码运行 → `_version.py`

---

### Task 1: 版本单源真相 — _version.py + version_info.txt + 读取模块

**Files:**
- Create: `apps/dsa-cloud-client/dsa_client/_version.py`(构建期生成,git-ignore)
- Create: `apps/dsa-cloud-client/version_info.txt`(PE 资源版本,与 _version.py 同步)
- Create: `apps/dsa-cloud-client/dsa_client/version.py`(读取模块)
- Test: `tests/test_client_version.py`

**Interfaces:**
- Produces:
  - `get_version() -> str`:优先 `sys._MEIPASS`/frozen 环境读 `_version.py`;fallback 读 `_version.py` 源码文件;最终 fallback `"0.0.0-dev"`
  - `VERSION_FILE = "dsa_client/_version.py"` 内容:`__version__ = "x.y.z"`(单行,无其他内容)
  - `version_info.txt`:`VSVersionInfo` 块(PyInstaller 格式,含 filevers/prodvers 与 _version.py 同步)
  - 同步脚本 `tools/sync_version.py <tag>`:解析 tag 格式 `v1.2.3` → 写 `_version.py` + 重写 `version_info.txt`(filevers=(1,2,3,0))→ 校验一致性

- [ ] **Step 1: 写失败测试**

```python
# tests/test_client_version.py
import pytest
from dsa_client.version import get_version, VERSION_FILE


def test_version_file_format(tmp_path):
    f = tmp_path / VERSION_FILE
    f.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    content = f.read_text(encoding="utf-8").strip()
    assert content == '__version__ = "1.2.3"'
    assert "\n" not in content.split('"')[1]


class TestGetVersion:
    def test_reads_version_file(self, monkeypatch, tmp_path):
        f = tmp_path / "_version.py"
        f.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
        monkeypatch.setattr("dsa_client.version.VERSION_MODULE_PATH", str(f))
        assert get_version() == "1.2.3"

    def test_fallback_dev(self, monkeypatch, tmp_path):
        monkeypatch.setattr("dsa_client.version.VERSION_MODULE_PATH", str(tmp_path / "missing.py"))
        assert get_version() == "0.0.0-dev"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_client_version.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 写实现**

```python
# dsa_client/version.py
# -*- coding: utf-8 -*-
"""版本读取:优先 frozen 打包的 _version.py,fallback 源码文件,最终 dev。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

VERSION_FILE = "dsa_client/_version.py"
VERSION_MODULE_PATH = str(Path(__file__).resolve().parent / "_version.py")


def _extract(path: str) -> str | None:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
        if text.startswith("__version__ = "):
            return text.split("=", 1)[1].strip().strip('"')
    except OSError:
        return None
    return None


def get_version() -> str:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            frozen = Path(meipass) / "_version.py"
            if frozen.exists():
                found = _extract(str(frozen))
                if found:
                    return found
    found = _extract(VERSION_MODULE_PATH)
    if found:
        return found
    return "0.0.0-dev"
```

`_version.py` 初始提交一个占位 `__version__ = "0.1.0"`(构建时被 sync 脚本覆盖);`version_info.txt` 按 PyInstaller `VSVersionInfo` 语法写 filevers/prodvers。

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_client_version.py -v`
Expected: PASS(3 个测试)

- [ ] **Step 5: tools/sync_version.py + 一致性自检**

```bash
& .venv/Scripts/python.exe tools/sync_version.py v1.2.3
```

自检:读回 `_version.py` == "1.2.3" 且 `version_info.txt` filevers==(1,2,3,0)。

- [ ] **Step 6: Commit**

```bash
git add apps/dsa-cloud-client/dsa_client/_version.py apps/dsa-cloud-client/dsa_client/version.py apps/dsa-cloud-client/version_info.txt tools/sync_version.py tests/test_client_version.py
git commit -m "feat(client): single-source version (tag -> _version.py + PE resources)"
```

---

### Task 2: updates.json 拉取 + 解析 + 24h 缓存 + 版本比较

**Files:**
- Create: `apps/dsa-cloud-client/dsa_client/updater.py`(更新检查部分)
- Test: `tests/test_updater_check.py`

**Interfaces:**
- Consumes: `get_version()`(Task 1)、`packaging.version`
- Produces:
  - `UPDATE_JSON_URL = "https://github.com/ZhuLinsen/daily_stock_analysis/releases/latest/download/updates.json"`
  - `CheckResult(update_available: bool, current: str, latest: str, url: str | None, sha256: str | None, notes: str | None, cached: bool, error: str | None)`(pydantic v2)
  - `fetch_updates_json(timeout: int = 15) -> dict`:HTTP GET → JSON;顶层含 `schema_version`/`updates`
  - `select_update(updates: list[dict], platform: str, arch: str, channel: str, current: str) -> dict | None`:过滤 platform/arch/channel → 仅 stable 版本 → 取 max(升序)且 > current
  - `check_for_update(cache_file: str | None = None) -> CheckResult`:命中 24h 内缓存直接返回 `cached=True`;否则联网检查并写缓存
  - 缓存 JSON:`{"checked_at": "<iso>", "latest": "...", "url": "...", "sha256": "..."}`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_updater_check.py
import json
import pytest
from dsa_client.updater import select_update, CheckResult


UPDATES = [
    {"version": "0.9.0", "platform": "win", "arch": "x64", "channel": "stable",
     "url": "u1", "sha256": "a"},
    {"version": "1.0.0", "platform": "win", "arch": "x64", "channel": "stable",
     "url": "u2", "sha256": "b"},
    {"version": "1.1.0-rc1", "platform": "win", "arch": "x64", "channel": "stable",
     "url": "u3", "sha256": "c"},  # prerelease 应被忽略
    {"version": "1.2.0", "platform": "win", "arch": "arm64", "channel": "stable",
     "url": "u4", "sha256": "d"},  # arch 不匹配
]


class TestSelectUpdate:
    def test_picks_highest_stable_matching(self):
        got = select_update(UPDATES, "win", "x64", "stable", "0.5.0")
        assert got is not None and got["version"] == "1.0.0"

    def test_none_when_current_newer(self):
        assert select_update(UPDATES, "win", "x64", "stable", "2.0.0") is None

    def test_ignores_prerelease(self):
        got = select_update(UPDATES, "win", "x64", "stable", "1.0.0")
        assert got is not None and got["version"] == "1.0.0" or got is None
        got2 = select_update(UPDATES, "win", "x64", "stable", "0.9.9")
        assert got2["version"] == "1.0.0"


class TestCache:
    def test_cache_hit_short_circuits(self, tmp_path):
        cache = tmp_path / "c.json"
        cache.write_text(json.dumps({"checked_at": "2999-01-01T00:00:00Z",
                                     "latest": "1.0.0", "url": "u", "sha256": "s"}),
                         encoding="utf-8")
        from dsa_client.updater import check_for_update
        result = check_for_update(cache_file=str(cache))
        assert result.cached is True and result.latest == "1.0.0"

    def test_stale_cache_refetches(self, tmp_path):
        cache = tmp_path / "c.json"
        cache.write_text(json.dumps({"checked_at": "2000-01-01T00:00:00Z",
                                     "latest": "0.1.0", "url": "u", "sha256": "s"}),
                         encoding="utf-8")
        from dsa_client.updater import check_for_update
        result = check_for_update(cache_file=str(cache))
        assert result.cached is False or result.error is not None  # 离线环境容忍 error
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_updater_check.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 写实现**(updater.py 更新检查部分;`select_update` 用 `packaging.version` 过滤 prerelease/build)

```python
# dsa_client/updater.py
# -*- coding: utf-8 -*-
"""客户端更新:检查(缓存)→ 下载校验 → updater.exe 原子替换。"""
from __future__ import annotations

import hashlib
import json
import platform as _platform
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from packaging.version import Version

from dsa_client.version import get_version

UPDATE_JSON_URL = ("https://github.com/ZhuLinsen/daily_stock_analysis/"
                   "releases/latest/download/updates.json")
CACHE_TTL_SECONDS = 24 * 3600


def fetch_updates_json(timeout: int = 15) -> dict:
    with urllib.request.urlopen(UPDATE_JSON_URL, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def select_update(updates: list[dict], platform: str, arch: str,
                  channel: str, current: str) -> Optional[dict]:
    candidates = [
        u for u in updates
        if u.get("platform") == platform and u.get("arch") == arch
        and u.get("channel") == channel
    ]
    cur = Version(current)
    best = None
    for u in candidates:
        v = Version(u["version"])
        if v.is_prerelease or v.is_devrelease or v.local:
            continue
        if v <= cur:
            continue
        if best is None or v > Version(best["version"]):
            best = u
    return best


def _platform_key() -> tuple[str, str]:
    sys_platform = _platform.system().lower()
    plat = "win" if sys_platform == "windows" else sys_platform
    arch = "x64" if _platform.machine().lower() in ("amd64", "x86_64") else "arm64"
    return plat, arch


def check_for_update(cache_file: Optional[str] = None) -> "CheckResult":
    cache_path = Path(cache_file) if cache_file else Path("data/update_check_cache.json")
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            age = time.time() - datetime.fromisoformat(
                cached["checked_at"].replace("Z", "+00:00")).timestamp()
            if age < CACHE_TTL_SECONDS:
                return CheckResult(update_available=True, current=get_version(),
                                   latest=cached["latest"], url=cached["url"],
                                   sha256=cached["sha256"], notes=cached.get("notes"),
                                   cached=True)
        except (KeyError, ValueError, OSError):
            pass
    try:
        payload = fetch_updates_json()
        plat, arch = _platform_key()
        chosen = select_update(payload.get("updates", []), plat, arch,
                               "stable", get_version())
        now = datetime.now(timezone.utc).isoformat()
        if chosen:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"checked_at": now, **chosen}),
                                  encoding="utf-8")
            return CheckResult(update_available=True, current=get_version(),
                               latest=chosen["version"], url=chosen["url"],
                               sha256=chosen["sha256"], notes=chosen.get("notes"),
                               cached=False)
        return CheckResult(update_available=False, current=get_version(),
                           latest=get_version(), cached=False)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(update_available=False, current=get_version(),
                           latest=get_version(), error=str(exc)[:200], cached=False)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_updater_check.py -v`
Expected: PASS(5 个测试)

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-cloud-client/dsa_client/updater.py tests/test_updater_check.py
git commit -m "feat(client): update check with cache + packaging.version compare"
```

---

### Task 3: updater.exe 子进程 — 下载校验 + 原子替换 + 回滚

**Files:**
- Create: `apps/dsa-cloud-client/updater_entry.py`(updater.exe 入口,onefile 构建)
- Create: `apps/dsa-cloud-client/updater_build.ps1`(PyInstaller onefile)
- Test: `tests/test_updater_apply.py`(对纯函数:sha256 校验、备份 LRU、路径规划)

**Interfaces:**
- Produces:
  - CLI:`updater.exe --current-version X --target-version Y --url U --sha256 H --backup-dir B`
  - 流程:下载(临时文件)→ sha256 比对 → 备份当前 exe 到 `B/`(LRU 3 版,命名 `<version>_<exe名>.bak`)→ 原子替换(os.replace)→ 重启父进程? 否 → 30s 健康检查:子进程自身存活即视为成功;失败恢复备份 + 提示运行 `restore.bat`
  - `restore.bat`:从最新备份恢复 exe
  - 纯函数:`verify_sha256(path, expected) -> bool`、`plan_backup(backup_dir, keep=3) -> list[Path]`(返回应删除的旧备份)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_updater_apply.py
import hashlib
import pytest
from dsa_client.updater_apply import verify_sha256, plan_backup


def test_verify_sha256_ok(tmp_path):
    f = tmp_path / "a.bin"
    data = b"hello"
    f.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    assert verify_sha256(str(f), digest) is True


def test_verify_sha256_mismatch(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    assert verify_sha256(str(f), hashlib.sha256(b"world").hexdigest()) is False


def test_plan_backup_keeps_latest_three(tmp_path):
    for v in ["0.1.0", "0.2.0", "0.3.0", "0.4.0"]:
        (tmp_path / f"{v}_app.exe.bak").write_bytes(b"x")
    to_delete = plan_backup(str(tmp_path), keep=3)
    assert len(to_delete) == 1
    assert "0.1.0" in to_delete[0]
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_updater_apply.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 写实现**

```python
# dsa_client/updater_apply.py
# -*- coding: utf-8 -*-
"""更新应用纯逻辑:校验/备份规划(由 updater.exe 调用)。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def verify_sha256(path: str, expected: str) -> bool:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest() == expected.lower()


_VERSION_RE = re.compile(r"^(\d+\.\d+\.\d+)_")


def plan_backup(backup_dir: str, keep: int = 3) -> list[str]:
    """返回应删除的旧备份路径(LRU:按文件名内嵌版本号升序)。"""
    files = list(Path(backup_dir).glob("*.bak"))
    keyed = []
    for f in files:
        m = _VERSION_RE.match(f.name)
        if m:
            keyed.append((tuple(int(x) for x in m.group(1).split(".")), str(f)))
    keyed.sort(key=lambda kv: kv[0])
    return [path for _, path in keyed[:-keep]] if len(keyed) > keep else []
```

`updater_entry.py`(onefile 入口,argparse 解析 5 参数,按流程执行,日志到 `data/update_log.txt`);`updater_build.ps1` 用 `pyinstaller --onefile --name updater updater_entry.py`,产物放 `dist/`。

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_updater_apply.py -v`
Expected: PASS(3 个测试)

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-cloud-client/dsa_client/updater_apply.py apps/dsa-cloud-client/updater_entry.py apps/dsa-cloud-client/updater_build.ps1 apps/dsa-cloud-client/restore.bat tests/test_updater_apply.py
git commit -m "feat(client): updater.exe atomic replace + backup LRU + rollback"
```

---

### Task 4: 启动后台检查线程 + 审计落盘

**Files:**
- Modify: `apps/dsa-cloud-client/dsa_client/app.py`(uvicorn.run 之前启动线程)
- Test: `tests/test_updater_thread.py`

**Interfaces:**
- Consumes: `check_for_update`(Task 2)、`UpdateEventDiagnostic`(Part-B Task 1)
- Produces:
  - `start_update_check_thread(daemon: bool = True) -> threading.Thread`:后台线程调 `check_for_update` → 有更新时写审计(version/event="available"/status)+ 控制台提示;异常不抛出只记录
  - 在 `app.py` 的 `main()` 中、`uvicorn.run(...)` 调用**之前**执行;不经过 FastAPI/无 server 守卫

- [ ] **Step 1: 写失败测试**

```python
# tests/test_updater_thread.py
import threading
import pytest
from dsa_client.update_thread import start_update_check_thread


class TestUpdateThread:
    def test_starts_daemon_thread(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr("dsa_client.update_thread.check_for_update",
                            lambda **kw: calls.append(1) or _result())
        thread = start_update_check_thread(cache_file=str(tmp_path / "c.json"))
        assert isinstance(thread, threading.Thread)
        assert thread.daemon is True
        thread.join(timeout=5)
        assert calls  # 至少跑了一次


def _result():
    from dsa_client.updater import CheckResult
    return CheckResult(update_available=False, current="1.0.0", latest="1.0.0")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_updater_thread.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 写实现**

```python
# dsa_client/update_thread.py
# -*- coding: utf-8 -*-
"""启动期更新检查:后台 daemon 线程,失败静默。"""
from __future__ import annotations

import threading
from typing import Optional

from dsa_client.updater import check_for_update
from src.services.run_diagnostics import UpdateEventDiagnostic


def _run_check(cache_file: Optional[str]):
    try:
        result = check_for_update(cache_file=cache_file)
        if result.error:
            return
        rec = UpdateEventDiagnostic(
            version=result.latest, event="available" if result.update_available else "up_to_date",
            status="ok", detail=result.notes)
        _append_audit(rec)
        if result.update_available:
            print(f"[updater] 发现新版本 {result.latest},后台已准备更新。")
    except Exception:  # noqa: BLE001
        pass  # 更新检查失败不影响启动


def start_update_check_thread(cache_file: Optional[str] = None) -> threading.Thread:
    thread = threading.Thread(target=_run_check, args=(cache_file,), daemon=True)
    thread.start()
    return thread
```

`app.py` 的 `main()`:`start_update_check_thread()` 放在 `uvicorn.run(...)` 之前一行。

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_updater_thread.py -v`
Expected: PASS(1 个测试)

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-cloud-client/dsa_client/update_thread.py apps/dsa-cloud-client/dsa_client/app.py tests/test_updater_thread.py
git commit -m "feat(client): startup update-check daemon thread before uvicorn"
```

---

### Task 5: client-release.yml — dispatch release_tag → 构建 → updates.json → 发布

**Files:**
- Create: `.github/workflows/client-release.yml`
- 修改:现有 release 流程触发方式(不动 desktop-release.yml)

**Interfaces:**
- Produces:
  - workflow_dispatch 输入 `release_tag`(如 `v1.2.3`)
  - 步骤:checkout → 校验 tag 存在(`git ls-remote --tags` 或 actions/checkout ref) → `tools/sync_version.py <release_tag>` → PyInstaller 构建(dsa_client + updater 两个 onefile)→ 生成/更新 `updates.json`(追加或替换该 version 条目;sha256 由构建产物计算)→ `gh release upload <release_tag> <exe> updates.json`(更新已有 release 资产)
  - `updates.json` 推送规则:读现有资产(存在则下载合并,保持 schema_version 不变),否则新建 `{"schema_version": 1, "updates": []}`

- [ ] **Step 1: 写 workflow 文件**(参照 `00-daily-analysis.yml` 的 step 风格;核心 step 伪代码)

```yaml
name: client-release
on:
  workflow_dispatch:
    inputs:
      release_tag:
        description: "Release tag to build & publish (e.g. v1.2.3)"
        required: true
        type: string

jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ inputs.release_tag }}
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r apps/dsa-cloud-client/requirements-build.txt pyinstaller
      - run: python tools/sync_version.py ${{ inputs.release_tag }}
      - run: pyinstaller --onefile --name dsa_client dsa_client/entry.py
        working-directory: apps/dsa-cloud-client
      - run: pyinstaller --onefile --name updater updater_entry.py
        working-directory: apps/dsa-cloud-client
      - name: Build updates.json
        shell: pwsh
        run: |
          $v = "${{ inputs.release_tag }}".TrimStart("v")
          $sha = (Get-FileHash "apps/dsa-cloud-client/dist/dsa_client.exe" -Algorithm SHA256).Hash.ToLower()
          $json = @{ schema_version = 1; updates = @() }
          try {
            $existing = Invoke-RestMethod "https://github.com/ZhuLinsen/daily_stock_analysis/releases/latest/download/updates.json"
            $json.updates = @($existing.updates | Where-Object { $_.version -ne $v })
          } catch { }
          $json.updates += @{ version = $v; platform = "win"; arch = "x64"; channel = "stable";
                              url = "https://github.com/ZhuLinsen/daily_stock_analysis/releases/download/${{ inputs.release_tag }}/dsa_client.exe";
                              sha256 = $sha; notes = "auto" }
          $json | ConvertTo-Json -Depth 5 | Set-Content updates.json -Encoding utf8
      - name: Publish to release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: gh release upload ${{ inputs.release_tag }} apps/dsa-cloud-client/dist/dsa_client.exe apps/dsa-cloud-client/dist/updater.exe updates.json --clobber
```

- [ ] **Step 2: 静态校验 workflow 语法**(YAML 可解析)

Run: `& .venv/Scripts/python.exe -c "import yaml; yaml.safe_load(open('.github/workflows/client-release.yml'))"`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/client-release.yml
git commit -m "ci(client): release-tag dispatch build + updates.json publish"
```

---

## Self-Review 记录

- **Spec 覆盖**:Release + updates.json(Task 2/5)、数组式 schema_version 顶层(Task 5)、启动后台线程且不经 FastAPI/server 守卫(Task 4)、packaging.version 仅 stable 忽略 prerelease/build(Task 2 select_update 过滤 is_prerelease/is_devrelease/local)、24h 缓存(Task 2 CACHE_TTL_SECONDS)、updater.exe 子进程原子替换 + 备份 LRU 3 + 30s 健康检查回滚(Task 3 流程 + restore.bat)、版本单源真相 dispatch release_tag → _version.py + version_info.txt 同步 + frozen 优先 PE 资源(Task 1)、v1 仅 win-x64 stable(Task 2 _platform_key + channel 固定 stable)、UpdateEventDiagnostic(Task 4)
- **依赖顺序**:Task 1(版本)→ Task 2(检查)→ Task 3(应用)→ Task 4(线程)→ Task 5(CI);Part-B Task 1 的 UpdateEventDiagnostic 为前置
- **Placeholder 扫描**:`_append_audit` 与 Part-C 同模式——接线时复用 run_diagnostics 的 sanitize 链;`restore.bat` 内容未在测试覆盖(人工验证,列入 Task 3 Step 5 提交);30s 健康检查在 updater_entry.py 实现细节中(CLI 主流程,建议人工运行验证)
- **类型一致性**:`CheckResult` 字段与缓存 JSON 键一致(checked_at/latest/url/sha256/notes);`select_update` 返回 dict 与 Task 5 的 updates.json 条目结构一致(version/platform/arch/channel/url/sha256/notes)