# -*- coding: utf-8 -*-
"""GitHub REST API 客户端:认证、429/5xx 退避重试、仓库与 Actions 操作。"""

from __future__ import annotations

import time

import requests

API_BASE = "https://api.github.com"
MAX_RETRIES = 4


class GitHubClient:
    def __init__(self, pat: str, session_factory=None, sleep=time.sleep):
        if session_factory is None:
            session_factory = requests.Session
        self._pat = pat
        self._session_factory = session_factory
        self.sleep = sleep

    def _new_session(self):
        s = self._session_factory()
        s.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {self._pat}",
        })
        return s

    def request(self, method: str, path: str, **kw):
        session = self._new_session()
        attempt = 0
        while True:
            resp = session.request(method, f"{API_BASE}{path}", timeout=30, **kw)
            if resp.status_code != 429 and resp.status_code < 500:
                resp.raise_for_status()
                if not resp.content:
                    return None
                return resp.json()
            attempt += 1
            if attempt > MAX_RETRIES:
                resp.raise_for_status()
            wait = 1.0 * (2 ** (attempt - 1))
            if resp.status_code == 429:
                wait = max(wait, float(resp.headers.get("Retry-After", 1)))
            self.sleep(wait)

    def get_user(self) -> dict:
        return self.request("GET", "/user")

    def get_repo_ok(self, owner: str, repo: str) -> bool:
        try:
            self.request("GET", f"/repos/{owner}/{repo}")
            return True
        except requests.HTTPError:
            return False

    def get_variable(self, owner: str, repo: str, name: str):
        try:
            return self.request("GET", f"/repos/{owner}/{repo}/actions/variables/{name}").get("value")
        except requests.HTTPError:
            return None

    def set_variable(self, owner: str, repo: str, name: str, value: str) -> None:
        self.request("PATCH", f"/repos/{owner}/{repo}/actions/variables/{name}",
                     json={"name": name, "value": value})

    def get_runs(self, owner: str, repo: str, limit: int = 5) -> list[dict]:
        return self.request("GET", f"/repos/{owner}/{repo}/actions/runs?per_page={limit}").get("workflow_runs", [])

    def dispatch(self, owner: str, repo: str, ref: str = "main", inputs: dict | None = None) -> None:
        self.request("POST", f"/repos/{owner}/{repo}/actions/workflows/00-daily-analysis.yml/dispatches",
                     json={"ref": ref, "inputs": inputs or {}})

    def list_artifacts(self, owner: str, repo: str, per_page: int = 10) -> list[dict]:
        return self.request("GET", f"/repos/{owner}/{repo}/actions/artifacts?per_page={per_page}").get(
            "artifacts", [])

    def download_artifact(self, owner: str, repo: str, artifact_id: int) -> bytes:
        session = self._new_session()
        resp = session.get(f"{API_BASE}/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip", timeout=120)
        resp.raise_for_status()
        return resp.content


def is_running(runs: list[dict]) -> bool:
    return any(r.get("status") in {"queued", "in_progress", "waiting"} for r in runs)