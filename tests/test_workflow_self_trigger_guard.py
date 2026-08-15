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


def _push_triggers_heartbeat(push: dict) -> bool:
    """push 触发配置是否会响应 meta/heartbeat 分支的 push。"""
    branches = push.get("branches")
    branches_ignore = push.get("branches-ignore")
    if branches is not None or branches_ignore is not None:
        if branches_ignore is not None:
            # branches-ignore:仅当心跳分支在忽略名单中才安全
            return not any(_match(b, HEARTBEAT_BRANCH) for b in branches_ignore)
        # branches 白名单:心跳分支不得命中
        return any(_match(b, HEARTBEAT_BRANCH) for b in branches)
    if push.get("tags"):
        return False  # 仅 tag 触发:分支 push 不可能触发
    return True  # 裸 push:所有分支都触发,含 meta/heartbeat


def test_push_trigger_safe_configs_exclude_heartbeat():
    assert _push_triggers_heartbeat({"branches": ["main"]}) is False
    assert _push_triggers_heartbeat({"branches-ignore": ["meta/heartbeat"]}) is False
    assert _push_triggers_heartbeat({"tags": ["v*"]}) is False
    assert _push_triggers_heartbeat({"branches": ["main"], "tags": ["v*"]}) is False


def test_push_trigger_unsafe_configs_flag_heartbeat():
    assert _push_triggers_heartbeat({}) is True  # 裸 push:所有分支都触发
    assert _push_triggers_heartbeat({"branches": ["meta/heartbeat"]}) is True
    assert _push_triggers_heartbeat({"branches": ["**"]}) is True


def test_no_workflow_triggered_by_heartbeat_branch_push():
    failures = []
    for name, wf in _load_all_workflows():
        push = _on(wf).get("push")
        if not push:
            continue
        if _push_triggers_heartbeat(push):
            failures.append(name)
    assert not failures, f"以下 workflow 会响应 meta/heartbeat push,需加 branches-ignore: {failures}"


def test_heartbeat_files_gitignored():
    content = (ROOT_DIR / ".gitignore").read_text(encoding="utf-8")
    assert "/.heartbeat-wt" in content, ".gitignore 需显式忽略心跳 worktree 目录 /.heartbeat-wt"
