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