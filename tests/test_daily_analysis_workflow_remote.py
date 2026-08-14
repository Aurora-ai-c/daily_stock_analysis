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
