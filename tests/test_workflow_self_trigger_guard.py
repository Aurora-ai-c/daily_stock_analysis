# -*- coding: utf-8 -*-
"""防护网:meta/heartbeat 分支的 push 不得触发任何 workflow。

心跳分支每天被 workflow 自动 push;若未来新增 push 触发类 workflow
未做分支过滤,会形成递归循环。本测试静态断言所有 workflow 的 push
触发条件均不匹配 meta/heartbeat。同时验证 .gitignore 忽略心跳
worktree 目录 .heartbeat-wt(本地运行/残留时不会被误提交)。
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
    assert "/.heartbeat-wt" in content, ".gitignore 需显式忽略心跳 worktree 目录 /.heartbeat-wt"
