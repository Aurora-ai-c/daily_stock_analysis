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
        if resp.status_code != 422:
            resp.raise_for_status()
        return resp


def resolve_repo_name(username: str) -> str:
    return f"dsa-cloud-{username.lower()}"


def check_template(api: GitHubApi, template_owner: str, template_repo: str) -> bool:
    resp = api.request("GET", f"/repos/{template_owner}/{template_repo}")
    if api.dry_run:
        return True
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


def list_secrets(api: GitHubApi, owner: str, repo: str) -> set[str]:
    resp = api.request("GET", f"/repos/{owner}/{repo}/actions/secrets")
    if api.dry_run:
        return set()
    return {item["name"] for item in resp.json().get("secrets", [])}


def _put_secret(api: GitHubApi, owner: str, repo: str, name: str, value: str) -> None:
    key_resp = api.request("GET", f"/repos/{owner}/{repo}/actions/secrets/public-key")
    if not isinstance(key_resp, requests.Response):
        api.request("PUT", f"/repos/{owner}/{repo}/actions/secrets/{name}",
                    json={"name": name, "value": value})
        return
    pubkey = key_resp.json()
    import base64

    from nacl import encoding, public
    seal = public.SealedBox(public.PublicKey(pubkey["key"], encoding.Base64Encoder()))
    encrypted = seal.encrypt(value.encode("utf-8"))
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
    print(f"2. 打开 exe 客户端,输入用户名 {args.owner} 与仓库名 {repo} 粘贴 PAT 登录")
    print(f"3. 仓库地址: https://github.com/{args.owner}/{repo}")
    print("===============================")


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
    parser.add_argument("--llm-key", default=None, help="LLM API Key(写入 LLM_PRIMARY_API_KEY secret)")
    parser.add_argument("--notify-webhook", default=None, help="通知 webhook(写入 CUSTOM_WEBHOOK_URLS secret)")
    parser.add_argument("--stock-list", default=None, help="默认自选股(写入 STOCK_LIST 变量)")
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

    secrets_map: dict[str, str] = {}
    if args.llm_key:
        secrets_map["LLM_PRIMARY_API_KEY"] = args.llm_key
    if args.notify_webhook:
        secrets_map["CUSTOM_WEBHOOK_URLS"] = args.notify_webhook
    write_secrets(api, args.owner, repo, secrets_map, args.overwrite_secrets, dry_run)
    set_variable(api, args.owner, repo, "STOCK_LIST", args.stock_list or "600519", dry_run)
    write_usage_guide(args, repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())