# DSA 云端客户端 — Part A: 模板仓库配套(workflow + 契约测试)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在模板仓库 `00-daily-analysis.yml` 加入远程控制所需能力(dispatch `stock_list` 输入 + heartbeat 续命),并用契约测试锁住行为。

**Architecture:** 纯 workflow YAML 改动 + 两个静态契约测试文件(仿照现有 `tests/test_daily_analysis_workflow_llm_env.py` 模式,用 yaml 解析 workflow 后断言)。heartbeat 用 `git worktree add` 在 `meta/heartbeat` 分支上追加提交并 push,顶层 workflow 保持 read-only,仅 analyze job 声明 `permissions: contents: write`。

**Tech Stack:** GitHub Actions YAML,Python 3.11(pytest 契约测试),bash step 脚本。

**关联 spec:** `docs/superpowers/specs/2026-08-14-dsa-cloud-client-design.md`(评审修订 v2)

## Global Constraints

- 不改变现有 schedule 触发行为、`concurrency: group: stock-analysis`(已存在于文件 27-29 行,勿动)
- dispatch 输入优先级:`stock_list`(本次) > 仓库变量 `STOCK_LIST` > 既有默认 `600519`;`STOCK_LIST_CONFIG` 变量名与既有兼容,不重命名
- heartbeat 步骤必须 `if: always()`(分析失败也要续命),失败不阻塞(job 不因此失败)
- heartbeat 分支名固定 `meta/heartbeat`;heartbeat 文件 `.gitignore` 忽略,提交用 `git add -f`
- 新契约测试必须离线运行(不调用 GitHub API),位于 `tests/`
- 测试命令:`python -m pytest tests/<file> -v`(Windows 用 `.venv\Scripts\python.exe`)

---

### Task 1: 远程控制契约测试(先写,失败)

**Files:**
- Create: `tests/test_daily_analysis_workflow_remote.py`

**Interfaces:**
- Consumes: `.github/workflows/00-daily-analysis.yml`(现有文件,不需新接口)
- Produces: 契约断言(后续 Task A2 需满足):
  - dispatch inputs 含 `stock_list`(type: string)
  - job `analyze` 顶层有 `permissions: {contents: write}`
  - steps 含名为 `heartbeat` 的步骤,位于"上传分析报告"之后
  - heartbeat 步骤的 `run` 脚本含 `meta/heartbeat`、`git worktree`、`heartbeat_health.json`、`git add -f`
  - `STOCK_LIST_CONFIG` env 含 `github.event.inputs.stock_list`
  - workflow 顶层含 `concurrency.group == stock-analysis` 且 `cancel-in-progress == false`

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""契约测试:DSA 云端远程控制对 00-daily-analysis.yml 的要求。

仿照 test_daily_analysis_workflow_llm_env.py 的静态解析模式,
离线运行,不调用 GitHub API。
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT_DIR / ".github/workflows/00-daily-analysis.yml"


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _analyze_job(wf: dict) -> dict:
    return wf["jobs"]["analyze"]


def _on(wf: dict) -> dict:
    """yaml 1.1 会把顶层 `on:` 解析为布尔键 True;两种可能都兜住。"""
    return wf.get("on") or wf.get(True) or {}


def test_dispatch_has_stock_list_input():
    inputs = _on(_load_workflow())["workflow_dispatch"]["inputs"]
    assert "stock_list" in inputs, "dispatch 需要 stock_list 输入(客户端触发时覆盖自选股)"
    assert inputs["stock_list"]["type"] == "string"
    assert inputs["stock_list"]["required"] is False


def test_analyze_job_scoped_permissions():
    job = _analyze_job(_load_workflow())
    perms = job.get("permissions", {})
    assert perms.get("contents") == "write", "heartbeat push 需要 job 级 contents: write"


def test_heartbeat_step_present_and_after_upload():
    steps = _analyze_job(_load_workflow())["steps"]
    names = [s.get("name", "") for s in steps]
    upload_idx = names.index("上传分析报告")
    hb_idx = names.index("heartbeat")
    assert hb_idx > upload_idx, "heartbeat 步骤应在上传报告之后"


def test_heartbeat_script_branch_and_health():
    steps = _analyze_job(_load_workflow())["steps"]
    hb = next(s for s in steps if s.get("name") == "heartbeat")
    script = hb["run"]
    assert "meta/heartbeat" in script
    assert "git worktree" in script, "用 worktree 在心跳分支追加提交"
    assert "heartbeat_health.json" in script
    assert "git add -f" in script, "heartbeat 文件被 .gitignore 忽略,需 -f 提交"
    assert hb.get("if") == "always()", "失败也要续命,不允许条件省略"


def test_stock_list_env_precedence():
    steps = _analyze_job(_load_workflow())["steps"]
    analyze = next(s for s in steps if s.get("name") == "执行股票分析")
    env = analyze["env"]["STOCK_LIST_CONFIG"]
    assert "github.event.inputs.stock_list" in env, "dispatch stock_list 必须最高优先"


def test_concurrency_group_unchanged():
    wf = _load_workflow()
    assert wf["concurrency"]["group"] == "stock-analysis"
    assert wf["concurrency"]["cancel-in-progress"] is False
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_daily_analysis_workflow_remote.py -v`
Expected: `test_dispatch_has_stock_list_input`、`test_analyze_job_scoped_permissions`、`test_heartbeat_step_present_and_after_upload`、`test_stock_list_env_precedence` FAIL(其余 heartbeat 步骤相关 FAIL 或 ERROR)

- [ ] **Step 3: Commit(红)**

```bash
git add tests/test_daily_analysis_workflow_remote.py
git commit -m "test: contract tests for remote control workflow changes (red)"
```

---

### Task 2: 修改 workflow 满足契约

**Files:**
- Modify: `.github/workflows/00-daily-analysis.yml`

**Interfaces:**
- Consumes: Task A1 契约测试
- Produces:
  - dispatch input `stock_list`(string, 可选, 默认空)
  - job `analyze` 顶部 `permissions: contents: write`(顶层不放权)
  - `STOCK_LIST_CONFIG` env 新优先级表达式
  - 新步骤 `heartbeat`(上传报告之后,`if: always()`)

- [ ] **Step 1: dispatch 增加 `stock_list` 输入**

在 `force_run` 输入之后(约 24 行)追加:

```yaml
      stock_list:
        description: '本次覆盖自选股（逗号分隔；留空则用仓库变量 STOCK_LIST）'
        required: false
        default: ''
        type: string
```

- [ ] **Step 2: job 级权限声明**

在 `jobs:` 下的 `analyze:` 与 `runs-on` 之间插入(约 32-33 行):

```yaml
    # heartbeat 续命需要推送到 meta/heartbeat 分支；仅此 job 放权，顶层保持 read-only
    permissions:
      contents: write
```

- [ ] **Step 3: STOCK_LIST_CONFIG 优先级改为 dispatch 优先**

第 421 行改为:

```yaml
          STOCK_LIST_CONFIG: ${{ github.event.inputs.stock_list || vars.STOCK_LIST || secrets.STOCK_LIST }}
```

- [ ] **Step 4: 新增 heartbeat 步骤**

在"上传分析报告"步骤(560-569 行)之后、"显示运行结果"之前插入:

```yaml
      - name: heartbeat
        if: always()
        env:
          HEARTBEAT_BRANCH: meta/heartbeat
          RUN_ID: ${{ github.run_id }}
          RUN_NUMBER: ${{ github.run_number }}
          JOB_STATUS: ${{ job.status }}
        run: |
          set +e
          mkdir -p data
          touch "data/heartbeat_$(date +%Y-%m-%d)"
          {
            echo "run_id=${RUN_ID}"
            echo "run_number=${RUN_NUMBER}"
            echo "mode=${{ github.event.inputs.mode || 'schedule' }}"
            echo "status=${JOB_STATUS}"
            echo "time=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
          } > "data/heartbeat_$(date +%Y-%m-%d)"
          python - <<'PYEOF'
          import json, os
          from datetime import datetime, timezone
          health = {
              "last_run_id": os.environ["RUN_ID"],
              "last_run_number": os.environ["RUN_NUMBER"],
              "last_status": os.environ["JOB_STATUS"],
              "last_update_at": datetime.now(timezone.utc).isoformat(),
          }
          with open("data/heartbeat_health.json", "w", encoding="utf-8") as fh:
              json.dump(health, fh, ensure_ascii=False, indent=2)
          PYEOF
          git config user.name "dsa-heartbeat[bot]"
          git config user.email "dsa-heartbeat[bot]@users.noreply.github.com"
          git fetch origin "${HEARTBEAT_BRANCH}" || true
          rm -rf .heartbeat-wt
          if git ls-remote --exit-code origin "${HEARTBEAT_BRANCH}" >/dev/null 2>&1; then
            git worktree add -f --detach .heartbeat-wt "origin/${HEARTBEAT_BRANCH}"
          else
            git worktree add --orphan -b "${HEARTBEAT_BRANCH}" .heartbeat-wt
          fi
          cp -f data/heartbeat_* .heartbeat-wt/
          git -C .heartbeat-wt add -f .
          if git -C .heartbeat-wt diff --cached --quiet; then
            echo "✅ heartbeat 无变更，跳过提交"
          else
          git -C .heartbeat-wt commit -m "heartbeat: run ${RUN_NUMBER} ${JOB_STATUS} $(date +%Y-%m-%d)"
          git push origin "HEAD:${HEARTBEAT_BRANCH}" || echo "⚠️ heartbeat push 失败（不阻塞主流程）"
          git worktree remove --force .heartbeat-wt 2>/dev/null || rm -rf .heartbeat-wt
          fi
```

- [ ] **Step 5: 运行契约测试确认转绿**

Run: `python -m pytest tests/test_daily_analysis_workflow_remote.py -v`
Expected: 全部 PASS(若 `git worktree add --orphan` 语法旧,heartbeat 脚本不影响测试,因为契约只查字符串存在)

- [ ] **Step 6: YAML 语法校验**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/00-daily-analysis.yml', encoding='utf-8')); print('YAML OK')"`
Expected: `YAML OK`

- [ ] **Step 7: 提交**

```bash
git add .github/workflows/00-daily-analysis.yml tests/test_daily_analysis_workflow_remote.py
git commit -m "feat(workflow): dispatch stock_list override + heartbeat renew via meta/heartbeat"
```

---

### Task 3: 自触发防护契约测试 + .gitignore

**Files:**
- Create: `tests/test_heartbeat_self_trigger_guard.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `.github/workflows/*.yml` 全部
- Produces:
  - 断言:任何 workflow 的 `on.push` 触达分支集合与 `meta/heartbeat` 不相交
  - `.gitignore` 显式忽略 `/data/heartbeat_*`

- [ ] **Step 1: 写失败测试(预期当前已通过,作为防护网)**

```python
# -*- coding: utf-8 -*-
"""防护网:meta/heartbeat 分支的 push 不得触发任何 workflow。

心跳分支每天被 workflow 自动 push;若未来新增 push 触发类 workflow
未做分支过滤,会形成递归循环。本测试静态断言所有 workflow 的 push
触发条件均不匹配 meta/heartbeat。
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT_DIR / ".github/workflows"

HEARTBEAT_BRANCH = "meta/heartbeat"


def _load_all_workflows() -> list[tuple[str, dict]]:
    wfs = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        wfs.append((path.name, yaml.safe_load(path.read_text(encoding="utf-8"))))
    assert wfs, "no workflows found"
    return wfs


def _match(value: object, ref: str) -> bool:
    """glob 匹配,含 * 与 **;ref 为待匹配分支名。"""
    if value in (None, ""):
        return False
    if value == ref:
        return True
    if isinstance(value, str) and any(ch in value for ch in "*?[") and (__import__("fnmatch").fnmatch(ref, value)):
        return True
    return False


def _on(wf: dict) -> dict:
    """yaml 1.1 把顶层 `on:` 解析为布尔键 True;两种可能都兜住。"""
    return wf.get("on") or wf.get(True) or {}


def test_no_workflow_triggered_by_heartbeat_branch_push():
    failures = []
    for name, wf in _load_all_workflows():
        push = _on(wf).get("push")
        if not push:
            continue
        branches = push.get("branches") or []
        branches_ignore = push.get("branches-ignore") or []
        tags = push.get("tags") or []
        if tags:
            continue  # 仅 tag 触发:分支 push 不可能触发
        hit = any(_match(b, HEARTBEAT_BRANCH) for b in branches)
        guarded = any(_match(b, HEARTBEAT_BRANCH) for b in branches_ignore)
        if hit and not guarded:
            failures.append(name)
    assert not failures, f"以下 workflow 会响应 meta/heartbeat push,需加 branches-ignore: {failures}"


def test_heartbeat_files_gitignored():
    content = (ROOT_DIR / ".gitignore").read_text(encoding="utf-8")
    assert "heartbeat" in content, ".gitignore 需显式忽略 /data/heartbeat_*"
```

- [ ] **Step 2: 运行确认通过**

Run: `python -m pytest tests/test_heartbeat_self_trigger_guard.py -v`
Expected: `test_no_workflow_triggered_by_heartbeat_branch_push` PASS(auto-tag 仅 main+paths-ignore、desktop-release 仅 tag、ci/pr-review 仅 PR);`test_heartbeat_files_gitignored` FAIL

- [ ] **Step 3: .gitignore 追加显式条目**

在 `/data/`(57 行)之后追加:

```
/data/heartbeat_*
```

- [ ] **Step 4: 再次运行确认全绿**

Run: `python -m pytest tests/test_heartbeat_self_trigger_guard.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_heartbeat_self_trigger_guard.py .gitignore
git commit -m "test: heartbeat self-trigger guard + gitignore heartbeat files"
```

---

## Part A 验收清单

- [ ] `python -m pytest tests/test_daily_analysis_workflow_remote.py tests/test_heartbeat_self_trigger_guard.py -v` 全绿
- [ ] `00-daily-analysis.yml` YAML 语法校验通过
- [ ] workflow 顶层无 `permissions` 放权(仅 analyze job 有)
- [ ] 用测试仓库实测一次:手动 dispatch → 报告上传后 heartbeat 步骤在 `meta/heartbeat` 分支产生 commit 且 `heartbeat_health.json` 内容正确
- [ ] heartbeat 分支 push 未触发任何其他 workflow 运行
