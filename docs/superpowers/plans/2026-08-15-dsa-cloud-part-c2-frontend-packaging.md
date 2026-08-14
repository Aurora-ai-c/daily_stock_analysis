# DSA 云端客户端 — Part C2: 前端四页 + 主入口 + PyInstaller 打包

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供浏览器端四页 UI(登录/自选股/触发/报告)与 DOMPurify 清洗、本地归档下载;实现 `app.py` 主入口(端口选择/浏览器打开/兜底)与 `build.ps1` PyInstaller onedir 打包脚本。

**Architecture:** 静态原生 HTML/JS/CSS(不走构建链),经 FastAPI(Part C1)挂载并从 Part C1 的 `/api/*` 读取。前端把 token 放 URL `#` hash 并从 hash 读取,避免明文在路径;报告 markdown 渲染前经 DOMPurify。`app.py` 启动 uvicorn(127.0.0.1,端口 49152-65535 冲突重试)并用浏览器打开 `http://127.0.0.1:<port>/#token=...`,打不开则打印 URL。打包用 PyInstaller `--onedir` + DOMPurify 作为 vendored 资源 `--add-data`。

**Tech Stack:** 原生 JS/CSS/HTML,DOMPurify(vendored 本地文件),Python(uvicorn/webbrowser),PyInstaller。

**关联 spec:** `docs/superpowers/specs/2026-08-14-dsa-cloud-client-design.md`(评审修订 v2)

## Global Constraints

- 前端不引入 npm/构建链;DOMPurify 以单文件 vendored 到 `static/vendor/DOMPurify.min.js`(来源见 Task C2.1 Step 4)
- token 只从浏览器地址栏 `#token=<值>` 读取;所有 `/api/*` 请求带 `?token=` 查询参数,状态变更带 `X-Origin-Token` 头
- 报告 markdown 渲染必须先 `DOMPurify.sanitize()`
- 端口:绑定 `127.0.0.1`,范围 `49152-65535`,被占则换端口重试 ≤3 次
- 日志写 `~/.dsa-cloud/server.log`
- 浏览器打开失败兜底:终端打印完整 URL(含 `#token=`)
- `build.ps1` 用 `--onedir`、`--add-data "static;static"`、`--version-file version_info.txt`
- 任务不联网验证 UI;验收以人工冒烟 + 现有 C1 API 契约测试为准

---

### Task C2.1: 静态前端四页(single index.html + app.js + style.css)

**Files:**
- Create: `apps/dsa-cloud-client/static/index.html`
- Create: `apps/dsa-cloud-client/static/app.js`
- Create: `apps/dsa-cloud-client/static/style.css`
- Create: `apps/dsa-cloud-client/static/vendor/DOMPurify.min.js`

**Interfaces:**
- Consumes: Part C1 `/api/*`(契约见 C1 Task C1.4)
- Produces: 四页视图(单页 tab)。核心函数:
  - `function getToken()` → 从 `location.hash` 解析 `#token=`
  - `let currentToken`(全局)
  - `async api(path, {method, body})` → fetch 带 token/headers;返回 JSON
  - `function renderReports(reports)` → 渲染报告卡片,含「下载到本地」按钮(调 `/api/reports/{id}/download`)
  - `function renderMarkdown(mdHtml)` → 用 DOMPurify sanitize 后注入

- [ ] **Step 1: 写 index.html(四页 tab 骨架)**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DSA 云端客户端</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header>
  <h1>DSA 云端客户端</h1>
  <button id="copy-url" title="复制访问地址">复制地址</button>
</header>
<nav>
  <button data-tab="login">登录</button>
  <button data-tab="watchlist">自选股</button>
  <button data-tab="trigger">触发</button>
  <button data-tab="reports">报告</button>
</nav>
<main>
  <section id="tab-login" class="tab">
    <h2>登录</h2>
    <label>GitHub 用户名 <input id="login-owner" placeholder="alice"></label>
    <label>仓库名 <input id="login-repo" placeholder="dsa-cloud-alice"></label>
    <label>PAT <input id="login-pat" type="password" placeholder="ghp_..."></label>
    <button id="login-save">保存</button>
    <p id="login-status"></p>
  </section>
  <section id="tab-watchlist" class="tab" hidden>
    <h2>自选股</h2>
    <textarea id="watchlist-input" rows="4" placeholder="逗号分隔，如 600519,600036"></textarea>
    <button id="watchlist-save">保存</button>
    <p id="watchlist-status"></p>
  </section>
  <section id="tab-trigger" class="tab" hidden>
    <h2>触发运行</h2>
    <label>模式
      <select id="trigger-mode">
        <option value="full">完整分析</option>
        <option value="stocks-only">仅股票</option>
        <option value="market-only">仅大盘</option>
      </select>
    </label>
    <label>本次覆盖自选股(可选) <input id="trigger-stock" placeholder="留空用仓库默认"></label>
    <button id="trigger-run">运行</button>
    <p id="trigger-status"></p>
  </section>
  <section id="tab-reports" class="tab" hidden>
    <h2>报告与信号</h2>
    <button id="reports-refresh">刷新</button>
    <div id="reports-list"></div>
  </section>
</main>
<script src="/static/vendor/DOMPurify.min.js"></script>
<script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: 写 app.js**

```javascript
"use strict";

let currentToken = "";
let currentReportHtml = "";

function getToken() {
  const m = location.hash.match(/#token=([^&]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

async function api(path, { method = "GET", body } = {}) {
  const url = `/api${path}?token=${encodeURIComponent(currentToken)}`;
  const init = { method, headers: { "X-Origin-Token": currentToken } };
  if (body !== undefined) init.body = JSON.stringify(body);
  const resp = await fetch(url, init);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

function show(elId, msg, ok = true) {
  const el = document.getElementById(elId);
  el.textContent = msg;
  el.className = ok ? "ok" : "err";
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== `tab-${name}`));
}

function renderReports(reports) {
  const box = document.getElementById("reports-list");
  box.innerHTML = "";
  if (!reports.length) { box.textContent = "暂无报告。"; return; }
  reports.forEach((r) => {
    const card = document.createElement("div");
    card.className = "report-card";
    card.innerHTML = `<strong>${DOMPurify.sanitize(r.name || "")}</strong>`;
    if (r.expired) {
      card.innerHTML += `<em>(已过期)</em>`;
    } else {
      const btn = document.createElement("button");
      btn.textContent = "下载到本地";
      btn.onclick = async () => {
        try { await api(`/reports/${r.id}/download`, { method: "GET" }); show("reports-list", "已存档到本地", true); }
        catch (e) { show("reports-list", "下载失败: " + e.message, false); }
      };
      card.appendChild(btn);
    }
    box.appendChild(card);
  });
}

function refreshReports() {
  api("/reports").then((d) => renderReports(d.reports))
    .catch((e) => show("reports-list", "获取报告失败: " + e.message, false));
}

document.getElementById("copy-url").onclick = async () => {
  try {
    await navigator.clipboard.writeText(location.href.replace(location.hash, `#token=${currentToken}`));
  } catch (e) { /* 忽略 */ }
};

document.querySelectorAll("nav button").forEach((b) =>
  b.onclick = () => switchTab(b.dataset.tab));

// 登录
document.getElementById("login-owner").value = localStorage.getItem("dsa_owner") || "";
document.getElementById("login-repo").value = localStorage.getItem("dsa_repo") || "";
document.getElementById("login-save").onclick = () => {
  const owner = document.getElementById("login-owner").value.trim();
  const repo = document.getElementById("login-repo").value.trim();
  const pat = document.getElementById("login-pat").value.trim();
  if (!owner || !repo || !pat) { show("login-status", "请填全三项", false); return; }
  localStorage.setItem("dsa_owner", owner);
  localStorage.setItem("dsa_repo", repo);
  // 构造配置写入本地:通过一个隐式 API(见 Step 4 说明)
  fetch(`/api/login?token=${encodeURIComponent(currentToken)}`, {
    method: "POST",
    headers: { "X-Origin-Token": currentToken, "Content-Type": "application/json" },
    body: JSON.stringify({ owner, repo, pat }),
  }).then((r) => r.json()).then((d) => {
    if (d.ok) show("login-status", "已保存。请重启应用生效。", true);
    else show("login-status", "保存失败", false);
  }).catch(() => show("login-status", "保存失败", false));
};

// 自选股
document.getElementById("watchlist-save").onclick = () => {
  const symbols = document.getElementById("watchlist-input").value.trim();
  api("/watchlist", { method: "PATCH", body: { symbols } })
    .then(() => show("watchlist-status", "已保存", true))
    .catch((e) => show("watchlist-status", "失败: " + e.message, false));
};

// 触发
document.getElementById("trigger-run").onclick = () => {
  const mode = document.getElementById("trigger-mode").value;
  const stock = document.getElementById("trigger-stock").value.trim();
  const body = { mode };
  if (stock) body.stock_list = stock;
  api("/trigger", { method: "POST", body })
    .then(() => show("trigger-status", "已触发运行", true))
    .catch((e) => show("trigger-status", "触发失败: " + e.message, false));
};

document.getElementById("reports-refresh").onclick = refreshReports;

// 初始化
currentToken = getToken();
if (currentToken) {
  api("/state").then((s) => {
    if (s.logged_in) {
      api("/watchlist").then((w) => (document.getElementById("watchlist-input").value = w.symbols || ""));
      refreshReports();
    } else {
      switchTab("login");
    }
  }).catch((e) => {
    switchTab("login");
    document.getElementById("login-status").textContent = "Token 无效: " + e.message;
    document.getElementById("login-status").className = "err";
  });
} else {
  switchTab("login");
}
```

> **注:** 登录用 `/api/login`(POST)持久化 owner/repo/PAT——该端点需在 Part C1 的 server.py 中补一个(见 Task C2.1 Step 4);它把 PAT 写进不可逆的本地配置(同样经 DPAPI)。为使前端契约完整,call 它需带 `X-Origin-Token`。

- [ ] **Step 3: 写 style.css**

```css
body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; margin: 0; color: #222; }
header { padding: 12px 20px; background: #1f6feb; color: #fff; display: flex; align-items: center; gap: 12px; }
nav { display: flex; gap: 8px; padding: 10px 20px; border-bottom: 1px solid #eee; }
nav button { border: 1px solid #ccc; background: #fff; padding: 6px 14px; border-radius: 6px; cursor: pointer; }
nav button:hover { background: #f0f6ff; }
main { max-width: 720px; margin: 20px auto; padding: 0 16px; }
.tab { display: flex; flex-direction: column; gap: 12px; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 14px; }
input, textarea, select { padding: 8px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; }
button { cursor: pointer; }
.ok { color: #1e7e34; }
.err { color: #c62828; }
.report-card { border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px; margin-bottom: 10px; }
.report-card button { margin-left: 8px; }
```

- [ ] **Step 4: 补充 C1 server.py 的 `/api/login` 端点**

在环境为非 Windows 或避免动 C1 提交可在此任务一并修改 `server.py`(此改动属跨任务耦合,遵照「一个任务一个可测试交付物」拆到本任务):

`apps/dsa-cloud-client/dsa_client/server.py` 新增(pydantic body 与其他端点一致):

```python
class LoginBody(BaseModel):
    owner: str
    repo: str
    pat: str
```

并在 `create_app` 内新增:

```python
    @app.post("/api/login")
    def api_login(request, body: LoginBody):
        if not (_guard(request) and _check_origin(request, config)):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        config.owner = body.owner
        config.repo = body.repo
        config.set_pat(body.pat)
        config.save()
        return {"ok": True}
```

并在 `tests/test_dsa_client_server.py` 追加:

```python
    def test_login_saves_config(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            file = Path(td) / "cfg.json"
            from unittest import mock
            with mock.patch.object(cfg_mod, "config_path", return_value=file):
                client, cfgv, _ = _make()
                r = client.post("/api/login?token=tok123", headers={"X-Origin-Token": "tok123"},
                                json={"owner": "bob", "repo": "dsa-cloud-bob", "pat": "ghp_secret"})
                assert r.status_code == 200
                assert r.json()["ok"] is True
                assert cfgv.get_pat() == "ghp_secret"
```

说明:`cfgv`(第 3 个返回值)是 `_make()` 返回的同一 `Config` 实例;`/api/login` 修改它后 `get_pat()` 走 DPAPI——Windows 下需真实 DPAPI。计划默认在 Windows 本机验收此测试。

- [ ] **Step 5: 运行后端测试确认无回归**

Run: `python -m pytest tests/test_dsa_client_server.py -v`
Expected: 全部 PASS(含新增 `test_login_saves_config`)

- [ ] **Step 6: 前端静态契约人工冒烟(可选,本任务不含浏览器自动化)**

Run: `python -c "from pathlib import Path; p=Path('apps/dsa-cloud-client/static/index.html'); print('vendor present:', (p.parent/'vendor'/'DOMPurify.min.js').exists()); print('app.js linked:', 'app.js' in p.read_text(encoding='utf-8'))"`
Expected: `vendor present: True` 与 `app.js linked: True`

- [ ] **Step 7: 提交**

```bash
git add apps/dsa-cloud-client/static/index.html apps/dsa-cloud-client/static/app.js apps/dsa-cloud-client/static/style.css apps/dsa-cloud-client/static/vendor/DOMPurify.min.js apps/dsa-cloud-client/dsa_client/server.py tests/test_dsa_client_server.py
git commit -m "feat(client): four-panel static UI with DOMPurify + login endpoint"
```

---

### Task C2.2: 主入口 `app.py`(端口选择 / 浏览器 / 兜底 / 日志)

**Files:**
- Create: `apps/dsa-cloud-client/app.py`

**Interfaces:**
- Consumes: `dsa_client.server.create_app`、`dsa_client.config.initialize_config`、uvicorn
- Produces:
  - `def pick_port() -> int`:在 49152-65535 随机选,用 socket 试绑定 127.0.0.1,重试 ≤3 次
  - `def main(argv=None) -> int`:加载配置、选端口、写 server.log、启动 uvicorn(127.0.0.1:port)、webbrowser.open,失败打印 URL
  - 断言 `config.validate()`:未登录时打印引导后退出码 0(仍打开页面让用户在 UI 登录)

- [ ] **Step 1: 写入口实现(无单测,人工冒烟)**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DSA 云端客户端主入口:绑定 127.0.0.1 随机端口,打开浏览器。"""

from __future__ import annotations

import argparse
import logging
import random
import socket
import sys
import webbrowser
from pathlib import Path

import uvicorn

from dsa_client import config as cfg
from dsa_client.server import create_app

PORT_MIN, PORT_MAX = 49152, 65535


def pick_port(retries: int = 3) -> int:
    last = None
    for _ in range(retries):
        port = random.randint(PORT_MIN, PORT_MAX)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError as e:
                last = e
                continue
    raise RuntimeError(f"Unable to bind any port in [{PORT_MIN},{PORT_MAX}]: {last}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser("dsa-cloud-client")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = cfg.initialize_config()
    except Exception as e:  # noqa: BLE001
        print(f"❌ 初始化配置失败: {e}", file=sys.stderr)
        return 1

    log_file = cfg.CONFIG_DIR / "server.log"
    logging.basicConfig(
        filename=log_file, level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.info("DSA client starting; logged_in=%s", not config.validate())

    app = create_app(config, static_dir=Path(__file__).resolve().parent / "static")
    port = args.port or pick_port()
    url = f"http://127.0.0.1:{port}/#token={config.token}"

    print(f"\n🔒 DSA 云端客户端已启动\n   地址: {url}\n   日志: {log_file}\n")
    print("   即将自动打开浏览器;若未打开,请手动复制上方地址访问。")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            print("⚠️ 无法自动打开浏览器,请手动访问: " + url)

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 语法与启动冒烟(可选 --no-browser + 极短超时)**

Run: `python -m py_compile apps/dsa-cloud-client/app.py && echo "compile OK"`
Expected: `compile OK`

Run: `PYTHONPATH=apps/dsa-cloud-client python apps/dsa-cloud-client/app.py --no-browser --port 59999 & sleep 6; curl -s http://127.0.0.1:59999/health; kill %1`
Expected: 输出健康体 `{"status":"ok"}` 后进程被终止(Windows PowerShell 用 Start-Process / Test-NetConnection 等价验证——见 Troubleshoot)

Troubleshoot(Win):PowerShell 下后台 job 与 kill 复杂;改用:
```powershell
$env:PYTHONPATH='apps/dsa-cloud-client'
Start-Process python -ArgumentList 'apps/dsa-cloud-client/app.py','--no-browser','--port','59999' -PassThru
Start-Sleep -Seconds 6
(Invoke-WebRequest http://127.0.0.1:59999/health).Content
Stop-Process -Name python -ErrorAction SilentlyContinue
```

- [ ] **Step 3: 提交**

```bash
git add apps/dsa-cloud-client/app.py
git commit -m "feat(client): app entry with port pick, browser open, server log"
```

---

### Task C2.3: PyInstaller 打包脚本

**Files:**
- Create: `apps/dsa-cloud-client/build.ps1`
- Create: `apps/dsa-cloud-client/version_info.txt`

**Interfaces:**
- Consumes: Part C1 包、`static/`(C2.1)、`app.py`(C2.2)
- Produces: `dist/dsa-cloud-client/`(onedir 可执行)

- [ ] **Step 1: 写 build.ps1**

```powershell
# 打包 DSA 云端客户端为 onedir 可执行(windows)。
# 用法: pwsh ./apps/dsa-cloud-client/build.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)  # 回到 apps/dsa-cloud-client

if (-not (Test-Path ".venv")) { Write-Warning "未找到 .venv,请先在仓库根目录创建虚拟环境并安装 requirements" }

pyinstaller `
  --noconfirm `
  --clean `
  --onedir `
  --name dsa-cloud-client `
  --version-file version_info.txt `
  --add-data "static;static" `
  --hidden-import uvicorn.logging `
  --hidden-import uvicorn.loops.auto `
  --hidden-import uvicorn.protocols.http.h11_auto `
  --hidden-import uvicorn.protocols.websockets.auto `
  --collect-submodules dsa_client `
  app.py

Write-Host "build complete: dist/dsa-cloud-client/dsa-cloud-client.exe"
```

- [ ] **Step 2: 写 version_info.txt**

```
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(0,1,0,0),
    prodvers=(0,1,0,0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0,0)),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'DSA'),
        StringStruct('FileDescription', 'DSA Cloud Client'),
        StringStruct('FileVersion', '0.1.0.0'),
        StringStruct('InternalName', 'dsa-cloud-client'),
        StringStruct('OriginalFilename', 'dsa-cloud-client.exe'),
        StringStruct('ProductName', 'DSA Cloud Client'),
        StringStruct('ProductVersion', '0.1.0.0')])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])])
```

- [ ] **Step 3: 验证脚本存在与语法(不实际打包)**

Run: `pwsh -Command "Get-Alias pyinstaller -ErrorAction SilentlyContinue; Test-Path apps/dsa-cloud-client/build.ps1"`
Expected: 脚本存在;若本机已装 pyinstaller,可走可选真打包(`pip install pyinstaller && pwsh apps/dsa-cloud-client/build.ps1`)

- [ ] **Step 4: 提交(不打包产物)**

```bash
git add apps/dsa-cloud-client/build.ps1 apps/dsa-cloud-client/version_info.txt
git commit -m "chore(client): PyInstaller onedir build script with version metadata"
```

---

## Part C2 验收清单

- [ ] `python -m pytest tests/test_dsa_client_server.py -v` 全绿(含 `/api/login`)
- [ ] `index.html` 引用了 `app.js` 与 `vendor/DOMPurify.min.js`(Step 6 检查通过)
- [ ] 主入口启动后 `/health` 返回 200;PowerShell 冒烟命令通过
- [ ] 浏览器打开失败时终端打印 URL(人工验证)
- [ ] `build.ps1` 存在且语法有效;`version_info.txt` 有效

---

## 全 Part C 最终 E2E 冒烟(可选,跨 C1/C2)

在本机真实 GitHub 测试仓库验证一次完整闭环:

```powershell
# 1. deploy_user 部署(真实,需测试账号 PAT)
python scripts/deploy_user.py --template-owner <tpl> --template-repo <tplr> ^
  --owner <test-acct> --pat <pat> --no-dry-run
# 2. 启动客户端并手动登录/自选股/触发
PYTHONPATH=apps/dsa-cloud-client python apps/dsa-cloud-client/app.py --no-browser
# 3. 触发后轮询 Actions,确认 signature: artifacts 生成,策略信号章节注入,heartbeat 分支更新
```