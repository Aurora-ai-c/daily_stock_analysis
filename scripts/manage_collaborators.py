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