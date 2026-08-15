# DSA 云端客户端 — Part B: 部署工具链(deploy_user + manage_collaborators)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供两个 CLI 脚本:为单用户从模板仓库生成私有仓库并完成配置(`deploy_user.py`),以及模板仓库协作者增删查(`manage_collaborators.py`),并配套部署文档 `docs/DEPLOYMENT.md`。

**Architecture:** 纯 Python CLI + requests 调 GitHub REST API。`deploy_user.py` 幂等(可重复执行),支持 `--dry-run`(全链路模拟输出、不发真实请求)、`--overwrite-secrets`(默认 merge 仅写不存在的)、`--heartbeat-test`(部署后触发最小运行验证)。单测用 `unittest.mock` mock requests,不联网。

**Tech Stack:** Python 3.11, requests(仓库已有依赖),pytest。

**关联 spec:** `docs/superpowers/specs/2026-08-14-dsa-cloud-client-design.md`(评审修订 v2,配套 B/C 节)

## Global Constraints

- 仓库已有 `requests>=2.31.0`,不新增依赖
- API 调用走 `https://api.github.com`,带 `Accept: application/vnd.github+json` 与 `X-GitHub-Api-Version: 2022-11-28` 头
- 幂等:重复执行不报错;generate 已存在时跳过并继续
- secrets 默认 merge(仅写不存在的),`--overwrite-secrets` 强制覆盖;`PUT /actions/secrets/{name}` 语义即覆盖
- 生成的仓库名固定格式 `dsa-cloud-<用户名小写>`(与 exe 客户端约定一致,见 Part C)
- 所有脚本必须可离线测试;任何真实网络调用只在函数内部,由测试 mock
- 测试命令:`python -m pytest tests/test_deploy_user.py tests/test_manage_collaborators.py -v`

---

### Task 1: `deploy_user.py` 骨架 + 前置检查 + generate + 启用 Actions

**Files:**
- Create: `scripts/deploy_user.py`
- Create: `tests/test_deploy_user.py`

**Interfaces:**
- Consumes: 环境/参数(见下方 CLI);GitHub REST API
- Produces(后续任务复用):
  - `class GitHubApi`:封装 `requests.Session`,方法 `get/put/patch/post/delete`,均带 auth 头;`request(method, path, **kw) -> requests.Response`
  - `def resolve_repo_name(username: str) -> str` → `f"dsa-cloud-{username.lower()}"`
  - `def check_template(api, template_owner, template_repo) -> bool`(GET 模板仓库,校验可见性/模板标记)
  - `def generate_repo(api, template_owner, template_repo, owner, repo_name, dry_run) -> str`("created" | "exists")
  - `def enable_actions(api, owner, repo_name, dry_run) -> None`
  - `def main(argv=None) -> int`:argparse 入口
  - CLI:`--template-owner`(必填)`--template-repo`(必填)`--owner`(用户)`--repo`(默认 `dsa-cloud-<owner>` )`--pat`(必填)`--dry-run`(默认开,`--no-dry-run` 才真跑)`--overwrite-secrets`(Task B2)`--heartbeat-test`(Task B3)

- [ ] **Step 1: 写失败测试(前置检查 + generate + 启用)**

```python
# -*- coding: utf-8 -*-
"""deploy_user.py 单元测试:mock requests,不联网。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import deploy_user  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _mk_api(monkeypatch=None):
    api = deploy_user.GitHubApi("dummy-pat")
    return api


class TestResolveRepoName:
    def test_lowercase_and_prefix(self):
        assert deploy_user.resolve_repo_name("Alice") == "dsa-cloud-alice"


class TestCheckTemplate:
    def test_ok(self):
        api = _mk_api()
        with mock.patch.object(api, "request", return_value=FakeResponse(200, {"is_template": True, "private": True, "permissions": {"pull": True}})):
            assert deploy_user.check_template(api, "tpl-owner", "tpl-repo") is True

    def test_missing_template_flag(self):
        api = _mk_api()
        with mock.patch.object(api, "request", return_value=FakeResponse(200, {"is_template": False})):
            with pytest.raises(RuntimeError, match="is_template"):
                deploy_user.check_template(api, "tpl-owner", "tpl-repo")


class TestGenerateRepo:
    def test_creates_and_posts(self):
        api = _mk_api()
        with mock.patch.object(api, "request", return_value=FakeResponse(201)) as req:
            assert deploy_user.generate_repo(api, "tpl-owner", "tpl-repo", "alice", "dsa-cloud-alice", dry_run=False) == "created"
        req.assert_called_once_with(
            "POST", "/repos/tpl-owner/tpl-repo/generate",
            json={"owner": "alice", "name": "dsa-cloud-alice", "private": True},
        )

    def test_exists_is_idempotent(self):
        api = _mk_api()
        with mock.patch.object(api, "request", return_value=FakeResponse(422)):
            assert deploy_user.generate_repo(api, "tpl-owner", "tpl-repo", "alice", "dsa-cloud-alice", dry_run=False) == "exists"

    def test_dry_run_does_not_post(self):
        api = _mk_api()
        with mock.patch.object(api, "request", return_value=FakeResponse(200)) as req:
            assert deploy_user.generate_repo(api, "tpl-owner", "tpl-repo", "alice", "dsa-cloud-alice", dry_run=True) == "created"
        req.assert_not_called()


class TestEnableActions:
    def test_puts_enabled(self):
        api = _mk_api()
        with mock.patch.object(api, "request", return_value=FakeResponse(204)) as req:
            deploy_user.enable_actions(api, "alice", "dsa-cloud-alice", dry_run=False)
        req.assert_called_once_with(
            "PUT", "/repos/alice/dsa-cloud-alice/actions/permissions",
            json={"enabled": True},
        )
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_deploy_user.py -v`
Expected: ERROR(`ModuleNotFoundError: No module named 'deploy_user'`)

- [ ] **Step 3: 最小实现**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署单个用户:DSA 云端客户端(模板仓库 → 用户私有仓库 + Actions 配置)。

幂等:可重复执行;--dry-run 默认开启,只打印将要执行的调用。
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

API_BASE = "https://api.github.com"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


class GitHubApi:
    def __init__(self, pat: str):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.session.headers["Authorization"] = f"Bearer {pat}"
        self.dry_run = False

    def request(self, method: str, path: str, **kw):
        if self.dry_run:
            print(f"[dry-run] {method} {path} {kw.get('json', '')}")
            return requests.Response()
        resp = self.session.request(method, f"{API_BASE}{path}", timeout=30, **kw)
        resp.raise_for_status()
        return resp


def resolve_repo_name(username: str) -> str:
    return f"dsa-cloud-{username.lower()}"


def check_template(api: GitHubApi, template_owner: str, template_repo: str) -> bool:
    resp = api.request("GET", f"/repos/{template_owner}/{template_repo}")
    data = resp.json()
    if not data.get("is_template"):
        raise RuntimeError(f"模板仓库 {template_owner}/{template_repo} 缺少 is_template 标记")
    if not data.get("private"):
        print("⚠️ 模板仓库为 public,建议改为 private")
    perms = data.get("permissions", {})
    if not perms.get("pull"):
        raise RuntimeError(f"PAT 对模板仓库 {template_owner}/{template_repo} 无读权限")
    return True


def generate_repo(api: GitHubApi, template_owner: str, template_repo: str,
                  owner: str, repo_name: str, dry_run: bool) -> str:
    payload = {"owner": owner, "name": repo_name, "private": True}
    if dry_run:
        print(f"[dry-run] POST /repos/{template_owner}/{template_repo}/generate {payload}")
        return "created"
    resp = api.request("POST", f"/repos/{template_owner}/{template_repo}/generate", json=payload)
    if resp.status_code in (201, 200):
        print(f"✅ 已生成仓库 {owner}/{repo_name}")
        return "created"
    if resp.status_code == 422:
        print(f"⏭️ 仓库已存在 {owner}/{repo_name},跳过")
        return "exists"
    resp.raise_for_status()
    return "unknown"


def enable_actions(api: GitHubApi, owner: str, repo_name: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] PUT /repos/{owner}/{repo_name}/actions/permissions enabled=true")
        return
    api.request("PUT", f"/repos/{owner}/{repo_name}/actions/permissions", json={"enabled": True})
    print(f"✅ Actions 已启用 {owner}/{repo_name}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="DSA 云端客户端 - 单用户部署")
    parser.add_argument("--template-owner", required=True)
    parser.add_argument("--template-repo", required=True)
    parser.add_argument("--owner", required=True, help="目标用户 GitHub 用户名")
    parser.add_argument("--repo", default=None, help="目标仓库名(默认 dsa-cloud-<owner>)")
    parser.add_argument("--pat", required=True, help="目标用户 PAT(带模板仓库读权限)")
    parser.add_argument("--dry-run", action="store_true", default=True, help="只打印调用(默认开启)")
    parser.add_argument("--no-dry-run", action="store_true", help="实际执行")
    parser.add_argument("--overwrite-secrets", action="store_true", help="覆盖已有 secrets(默认 merge)")
    parser.add_argument("--heartbeat-test", action="store_true", help="部署后触发最小运行验证 heartbeat")
    args = parser.parse_args(argv)

    if args.dry_run and args.no_dry_run:
        print("⚠️ 同时指定 --dry-run 与 --no-dry-run,--no-dry-run 生效")
        args.dry_run = False

    dry_run = not args.no_dry_run
    if not dry_run:
        print("🚨 即将真实执行部署。按 Ctrl+C 可在任意步骤前中止。")
        input("按回车继续…")

    api = GitHubApi(args.pat)
    api.dry_run = dry_run
    repo = args.repo or resolve_repo_name(args.owner)

    check_template(api, args.template_owner, args.template_repo)
    generate_repo(api, args.template_owner, args.template_repo, args.owner, repo, dry_run)
    enable_actions(api, args.owner, repo, dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

注意:若走 `--no-dry-run` 真实执行,`generate_repo` 需要处理 4xx 时 `raise_for_status` 的坑——上面实现里 201/200/422 分支外的 `resp.raise_for_status()` 在 4xx 会抛错,符合预期(网络错误不应静默)。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_deploy_user.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/deploy_user.py tests/test_deploy_user.py
git commit -m "feat(deploy): deploy_user.py scaffold with dry-run, template check, generate, enable actions"
```

---

### Task 2: secrets/变量 merge 逻辑 + 指引输出

**Files:**
- Modify: `scripts/deploy_user.py`
- Modify: `tests/test_deploy_user.py`

**Interfaces:**
- Consumes: `GitHubApi`、`dry_run` 标志(来自 B1)
- Produces:
  - `def list_secrets(api, owner, repo) -> set[str]`
  - `def write_secrets(api, owner, repo, secrets: dict[str, str], overwrite: bool, dry_run: bool) -> list[str]`(返回已写入的 key)
  - `def set_variable(api, owner, repo, name, value, dry_run) -> None`
  - `def write_usage_guide(args, repo, dry_run) -> None`(打印使用指引)
  - CLI 参数:`--llm-key`、`--notify-webhook`(可选,缺失则跳过)

- [ ] **Step 1: 追加失败测试**

```python
class TestWriteSecrets:
    def _api(self):
        return _mk_api()

    def test_merge_skips_existing(self):
        api = self._api()
        # 调用序列:GET secrets(发现 LLM_API_KEY 已存在) → GET public-key → PUT WEBHOOK_URL
        api.request = mock.Mock(side_effect=[
            FakeResponse(200, {"secrets": [{"name": "LLM_API_KEY"}]}),
            FakeResponse(200, {"key": "k", "key_id": "1"}),
            FakeResponse(201),
        ])
        written = deploy_user.write_secrets(api, "alice", "dsa-cloud-alice",
                                            {"LLM_API_KEY": "new", "WEBHOOK_URL": "https://x"}, overwrite=False, dry_run=False)
        assert written == ["WEBHOOK_URL"]
        puts = [c.args[0] for c in api.request.call_args_list if c.args[0] == "PUT"]
        assert puts == ["/repos/alice/dsa-cloud-alice/actions/secrets/WEBHOOK_URL"]

    def test_overwrite_writes_all(self):
        api = self._api()
        with mock.patch.object(api, "request", side_effect=[
            FakeResponse(200, {"secrets": [{"name": "LLM_API_KEY"}]}),
            FakeResponse(200, {"key": "k", "key_id": "1"}),
            FakeResponse(201),
            FakeResponse(200, {"key": "k", "key_id": "1"}),
            FakeResponse(201),
        ]) as req:
            written = deploy_user.write_secrets(api, "alice", "dsa-cloud-alice",
                                                {"LLM_API_KEY": "new", "WEBHOOK_URL": "u"}, overwrite=True, dry_run=False)
        assert sorted(written) == ["LLM_API_KEY", "WEBHOOK_URL"]
        puts = [c.args[0] for c in req.call_args_list if c.args[0] == "PUT"]
        assert len(puts) == 2


class TestSetVariable:
    def test_patch_variable(self):
        api = _mk_api()
        with mock.patch.object(api, "request", return_value=FakeResponse(201)) as req:
            deploy_user.set_variable(api, "alice", "dsa-cloud-alice", "STOCK_LIST", "600519,600036", dry_run=False)
        req.assert_called_once_with(
            "PATCH", "/repos/alice/dsa-cloud-alice/actions/variables/STOCK_LIST",
            json={"name": "STOCK_LIST", "value": "600519,600036"},
        )
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_deploy_user.py::TestWriteSecrets tests/test_deploy_user.py::TestSetVariable -v`
Expected: FAIL(`AttributeError: module 'deploy_user' has no attribute 'write_secrets'`)

- [ ] **Step 3: 实现 merge/变量/指引**

在 `deploy_user.py` 追加:

```python
def list_secrets(api: GitHubApi, owner: str, repo: str) -> set[str]:
    resp = api.request("GET", f"/repos/{owner}/{repo}/actions/secrets")
    return {item["name"] for item in resp.json().get("secrets", [])}


def _put_secret(api: GitHubApi, owner: str, repo: str, name: str, value: str) -> None:
    pubkey = api.request("GET", f"/repos/{owner}/{repo}/actions/secrets/public-key").json()
    import base64

    from nacl import encoding, public, utils
    seal = public.SealedBox(public.PublicKey(pubkey["key"], encoding.Base64Encoder()))
    encrypted = seal.encrypt(value.encode("utf-8"), utils.random(public.SealedBox.NONCE_SIZE))
    api.request(
        "PUT", f"/repos/{owner}/{repo}/actions/secrets/{name}",
        json={"encrypted_value": base64.b64encode(encrypted).decode("ascii"),
              "key_id": pubkey["key_id"]},
    )


def write_secrets(api: GitHubApi, owner: str, repo: str,
                  secrets: dict[str, str], overwrite: bool, dry_run: bool) -> list[str]:
    existing = set() if overwrite else list_secrets(api, owner, repo)
    written: list[str] = []
    for name, value in secrets.items():
        if name in existing and not overwrite:
            print(f"⏭️ secret {name} 已存在,跳过(--overwrite-secrets 强制覆盖)")
            continue
        if dry_run:
            print(f"[dry-run] PUT /repos/{owner}/{repo}/actions/secrets/{name}")
        else:
            _put_secret(api, owner, repo, name, value)
        written.append(name)
    return written


def set_variable(api: GitHubApi, owner: str, repo: str, name: str, value: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] PATCH /repos/{owner}/{repo}/actions/variables/{name} = {value}")
        return
    api.request("PATCH", f"/repos/{owner}/{repo}/actions/variables/{name}",
                json={"name": name, "value": value})
    print(f"✅ 变量 {name} 已设置")


def write_usage_guide(args: argparse.Namespace, repo: str) -> None:
    print("\n========== 使用指引 ==========")
    print(f"1. 让用户创建 PAT(权限: repo + workflow):")
    print("   https://github.com/settings/tokens/new?scopes=repo,workflow")
    print("2. 打开 exe 客户端,输入用户名 {args.owner} 与仓库名 {repo} 粘贴 PAT 登录")
    print(f"3. 仓库地址: https://github.com/{args.owner}/{repo}")
    print("===============================")
```

- [ ] **Step 4: main 接线**

在 `main()` 中 `enable_actions` 之后追加:

```python
    secrets_map: dict[str, str] = {}
    if args.llm_key:
        secrets_map["LLM_PRIMARY_API_KEY"] = args.llm_key
    if args.notify_webhook:
        secrets_map["CUSTOM_WEBHOOK_URLS"] = args.notify_webhook
    write_secrets(api, args.owner, repo, secrets_map, args.overwrite_secrets, dry_run)
    set_variable(api, args.owner, repo, "STOCK_LIST", args.stock_list or "600519", dry_run)
    write_usage_guide(args, repo)
```

并在 argparse 追加:

```python
    parser.add_argument("--llm-key", default=None, help="LLM API Key(写入 LLM_PRIMARY_API_KEY secret)")
    parser.add_argument("--notify-webhook", default=None, help="通知 webhook(写入 CUSTOM_WEBHOOK_URLS secret)")
    parser.add_argument("--stock-list", default=None, help="默认自选股(写入 STOCK_LIST 变量)")
```

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest tests/test_deploy_user.py -v`
Expected: 全部 PASS(注意:写入 secrets 的 PUT 顺序断言——上面 `test_merge_skips_existing` 依赖 dict 顺序,`{"LLM_API_KEY": ..., "WEBHOOK_URL": ...}` 在 Python 3.7+ 保持插入序;`req.call_args_list` 中先 GET secrets、GET public-key、PUT。mock `side_effect` 序列需与此一致——测试中 GET public-key 返回 `{"key": "k", "key_id": "1"}`,`PUT` 返回 201。若失败,检查 side_effect 顺序与调用次数是否匹配)

- [ ] **Step 6: 提交**

```bash
git add scripts/deploy_user.py tests/test_deploy_user.py
git commit -m "feat(deploy): merge secrets, STOCK_LIST variable, usage guide"
```

---

### Task 3: `--heartbeat-test` + 幂等复核测试

**Files:**
- Modify: `scripts/deploy_user.py`
- Modify: `tests/test_deploy_user.py`

**Interfaces:**
- Consumes: `GitHubApi`(B1)
- Produces:
  - `def heartbeat_test(api, owner, repo, dry_run) -> None`:dispatch `00-daily-analysis.yml` mode=stocks-only,然后打印说明(手动查看 heartbeat 分支)
  - `def run_deploy(argv) -> int`:把 main 中流程抽成可测函数(不含 argparse/交互)

- [ ] **Step 1: 追加失败测试(幂等 + heartbeat-test)**

```python
class TestHeartbeatTest:
    def test_dispatches_workflow(self):
        api = _mk_api()
        with mock.patch.object(api, "request", return_value=FakeResponse(204)) as req:
            deploy_user.heartbeat_test(api, "alice", "dsa-cloud-alice", dry_run=False)
        req.assert_called_once_with(
            "POST", "/repos/alice/dsa-cloud-alice/actions/workflows/00-daily-analysis.yml/dispatches",
            json={"ref": "main", "inputs": {"mode": "stocks-only"}},
        )


class TestRunDeploy:
    def test_full_flow_calls_in_order(self):
        api = _mk_api()
        api.dry_run = False
        calls = []

        def _fake_request(method, path, **kw):
            calls.append((method, path))
            if method == "GET" and path.startswith("/repos/tpl/"):
                return FakeResponse(200, {"is_template": True, "private": True, "permissions": {"pull": True}})
            return FakeResponse(200, {"secrets": []})

        api.request = mock.Mock(side_effect=_fake_request)
        deploy_user.run_deploy(api, deploy_user.DeployArgs(
            template_owner="tpl", template_repo="tplr", owner="alice",
            repo="dsa-cloud-alice", llm_key=None, notify_webhook=None,
            stock_list=None, overwrite_secrets=False, heartbeat_test=False,
        ))
        methods = [m for m, _ in calls]
        assert methods[0] == "GET"          # check_template
        assert methods[1] == "POST"         # generate
        assert methods[2] == "PUT"          # enable actions
        assert "PATCH" in methods           # STOCK_LIST 变量
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_deploy_user.py::TestHeartbeatTest tests/test_deploy_user.py::TestRunDeploy -v`
Expected: FAIL(`AttributeError`)

- [ ] **Step 3: 重构 main → run_deploy + 实现 heartbeat_test**

```python
def heartbeat_test(api: GitHubApi, owner: str, repo: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] POST .../workflows/00-daily-analysis.yml/dispatches mode=stocks-only")
        return
    api.request(
        "POST", f"/repos/{owner}/{repo}/actions/workflows/00-daily-analysis.yml/dispatches",
        json={"ref": "main", "inputs": {"mode": "stocks-only"}},
    )
    print("✅ 已触发验证运行(mode=stocks-only)。")
    print(f"   完成后检查 https://github.com/{owner}/{repo}/tree/meta/heartbeat")


def run_deploy(api: GitHubApi, args: "DeployArgs") -> None:
    check_template(api, args.template_owner, args.template_repo)
    generate_repo(api, args.template_owner, args.template_repo, args.owner, args.repo, api.dry_run)
    enable_actions(api, args.owner, args.repo, api.dry_run)
    secrets_map: dict[str, str] = {}
    if args.llm_key:
        secrets_map["LLM_PRIMARY_API_KEY"] = args.llm_key
    if args.notify_webhook:
        secrets_map["CUSTOM_WEBHOOK_URLS"] = args.notify_webhook
    write_secrets(api, args.owner, args.repo, secrets_map, args.overwrite_secrets, api.dry_run)
    set_variable(api, args.owner, args.repo, "STOCK_LIST", args.stock_list or "600519", api.dry_run)
    if args.heartbeat_test:
        heartbeat_test(api, args.owner, args.repo, api.dry_run)
    write_usage_guide(args, args.repo)


class DeployArgs:
    def __init__(self, template_owner: str, template_repo: str, owner: str, repo: str,
                 llm_key=None, notify_webhook=None, stock_list=None,
                 overwrite_secrets=False, heartbeat_test=False):
        self.template_owner = template_owner
        self.template_repo = template_repo
        self.owner = owner
        self.repo = repo
        self.llm_key = llm_key
        self.notify_webhook = notify_webhook
        self.stock_list = stock_list
        self.overwrite_secrets = overwrite_secrets
        self.heartbeat_test = heartbeat_test
```

`main()` 中 `parse_args` 后用 `DeployArgs` 组装并调用 `run_deploy`(删除原内联流程,避免重复逻辑)。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_deploy_user.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/deploy_user.py tests/test_deploy_user.py
git commit -m "feat(deploy): heartbeat-test trigger + run_deploy refactor"
```

---

### Task 4: `manage_collaborators.py`

**Files:**
- Create: `scripts/manage_collaborators.py`
- Create: `tests/test_manage_collaborators.py`

**Interfaces:**
- Produces:
  - CLI:`list|add|remove --owner --repo --pat [--user USER] [--permission PULL]`
  - `def list_collaborators(api, owner, repo) -> list[dict]`
  - `def add_collaborator(api, owner, repo, username, permission) -> None`
  - `def remove_collaborator(api, owner, repo, username) -> None`

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""manage_collaborators.py 单元测试:mock requests,不联网。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import manage_collaborators as mc  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json


def _api():
    api = mc.GitHubApi("pat")
    api.request = mock.Mock(return_value=FakeResponse(200, {"permission": "pull"}))
    return api


def test_list_collaborators():
    api = mc.GitHubApi("pat")
    api.request = mock.Mock(return_value=FakeResponse(200, [{"login": "alice", "permissions": {"push": True}}]))
    result = mc.list_collaborators(api, "tpl", "dsa-cloud")
    assert result[0]["login"] == "alice"
    api.request.assert_called_once_with("GET", "/repos/tpl/dsa-cloud/collaborators?permission=all")


def test_add_collaborator_puts_pull():
    api = mc.GitHubApi("pat")
    api.request = mock.Mock(return_value=FakeResponse(201, {"permission": "pull"}))
    mc.add_collaborator(api, "tpl", "dsa-cloud", "alice", "pull")
    api.request.assert_called_once_with(
        "PUT", "/repos/tpl/dsa-cloud/collaborators/alice",
        json={"permission": "pull"},
    )


def test_remove_collaborator_deletes():
    api = mc.GitHubApi("pat")
    api.request = mock.Mock(return_value=FakeResponse(204))
    mc.remove_collaborator(api, "tpl", "dsa-cloud", "alice")
    api.request.assert_called_once_with("DELETE", "/repos/tpl/dsa-cloud/collaborators/alice")
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_manage_collaborators.py -v`
Expected: ERROR(`ModuleNotFoundError`)

- [ ] **Step 3: 实现**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模板仓库协作者管理:list / add / remove。"""

from __future__ import annotations

import argparse
import sys

import requests

API_BASE = "https://api.github.com"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


class GitHubApi:
    def __init__(self, pat: str):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.session.headers["Authorization"] = f"Bearer {pat}"

    def request(self, method: str, path: str, **kw):
        resp = self.session.request(method, f"{API_BASE}{path}", timeout=30, **kw)
        resp.raise_for_status()
        return resp


def list_collaborators(api: GitHubApi, owner: str, repo: str) -> list[dict]:
    return api.request("GET", f"/repos/{owner}/{repo}/collaborators?permission=all").json()


def add_collaborator(api: GitHubApi, owner: str, repo: str, username: str, permission: str = "pull") -> None:
    api.request("PUT", f"/repos/{owner}/{repo}/collaborators/{username}", json={"permission": permission})
    print(f"✅ 已添加协作者 {username} (permission={permission})")


def remove_collaborator(api: GitHubApi, owner: str, repo: str, username: str) -> None:
    api.request("DELETE", f"/repos/{owner}/{repo}/collaborators/{username}")
    print(f"✅ 已移除协作者 {username}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="模板仓库协作者管理")
    sub = parser.add_subparsers(dest="action", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--owner", required=True)
    common.add_argument("--repo", required=True)
    common.add_argument("--pat", required=True)
    p_list = sub.add_parser("list", parents=[common])
    p_add = sub.add_parser("add", parents=[common])
    p_add.add_argument("--user", required=True)
    p_add.add_argument("--permission", default="pull", choices=["pull", "push", "admin"])
    p_rm = sub.add_parser("remove", parents=[common])
    p_rm.add_argument("--user", required=True)
    args = parser.parse_args(argv)

    api = GitHubApi(args.pat)
    if args.action == "list":
        for item in list_collaborators(api, args.owner, args.repo):
            perms = item.get("permissions", {})
            level = "admin" if perms.get("admin") else ("push" if perms.get("push") else "pull")
            print(f"{item['login']}\t{level}")
    elif args.action == "add":
        add_collaborator(api, args.owner, args.repo, args.user, args.permission)
    elif args.action == "remove":
        remove_collaborator(api, args.owner, args.repo, args.user)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_manage_collaborators.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/manage_collaborators.py tests/test_manage_collaborators.py
git commit -m "feat(deploy): manage_collaborators.py add/list/remove"
```

---

### Task 5: `docs/DEPLOYMENT.md`

**Files:**
- Create: `docs/DEPLOYMENT.md`

**Interfaces:**
- Consumes: 全部 B1-B4 CLI;Part A 的 heartbeat 行为;spec 配套 C 节

- [ ] **Step 1: 写文档**

```markdown
# DSA 云端客户端部署

## 部署前

1. 管理员把用户添加为模板仓库协作者(读权限):
   `python scripts/manage_collaborators.py add --owner <tpl-owner> --repo <tpl-repo> --pat <admin-pat> --user <username>`
2. 用户创建 PAT,最小权限 `repo` + `workflow`:
   https://github.com/settings/tokens/new?scopes=repo,workflow
   - 2FA 用户:经典 PAT 仍可用,但建议 fine-grained PAT(见下)
   - 若需完整配额卡片,额外勾选 `user`
   - 泄露应急:立即到 https://github.com/settings/tokens 撤销该 PAT
3. 模板仓库须为 private 且勾选 "Template repository"。

## 用户自部署(交互)

```bash
python scripts/deploy_user.py \
  --template-owner <tpl-owner> --template-repo <tpl-repo> \
  --owner <username> --pat <user-pat> \
  --llm-key <可选> --notify-webhook <可选> --stock-list 600519,600036 \
  --no-dry-run
```

## 代理部署(用户交付 PAT)

同上命令,由部署 agent 执行。授权边界:只在本次部署用途内使用,PAT 用完即废。

## 常用选项

- `--dry-run`(默认):只打印调用,不真正执行
- `--overwrite-secrets`:强制覆盖已有 secrets(默认 merge,只写不存在的)
- `--heartbeat-test`:部署后触发一次 stocks-only 运行,验证 heartbeat 续命链路
- 再次部署幂等:重复执行会跳过已存在仓库与已有 secrets

## 部署后验证

1. 打开 https://github.com/<owner>/dsa-cloud-<owner>/actions,确认 Actions 运行
2. 确认 `meta/heartbeat` 分支出现 heartbeat 提交:
   https://github.com/<owner>/dsa-cloud-<owner>/tree/meta/heartbeat
3. 打开 exe 客户端,输入用户名/仓库名/PAT 登录

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| generate 返回 422 | 仓库已存在 | 正常,幂等跳过 |
| Actions 不运行 | 未启用 / 未安装 | 重跑 deploy(enable_actions) |
| heartbeat 分支无更新 | 仓库默认 workflow 权限被限制为 read-only 且拒绝 job 级提升 | Settings → Actions → Workflow permissions 允许提升;或改用 fine-grained PAT |
| 配额超限 | private 2000 分钟/月 | 等月重置或升级计划 |
```

- [ ] **Step 2: 自检文档内部命令一致性**

Run: `python -m pytest tests/test_deploy_user.py tests/test_manage_collaborators.py -v`
Expected: 全部 PASS(确认脚本参数与文档一致)

- [ ] **Step 3: 提交**

```bash
git add docs/DEPLOYMENT.md
git commit -m "docs(deploy): deployment guide with PAT security and troubleshooting"
```

---

## Part B 验收清单

- [ ] `python -m pytest tests/test_deploy_user.py tests/test_manage_collaborators.py -v` 全绿
- [ ] `python scripts/deploy_user.py --help` 正常输出全部参数
- [ ] `python scripts/deploy_user.py --template-owner x --template-repo y --owner alice --pat z`(dry-run 默认)打印完整调用序列且不发真实请求
- [ ] `python scripts/manage_collaborators.py list --owner x --repo y --pat z` 在测试仓库实测成功
- [ ] 文档命令与脚本参数一致(自检通过)
