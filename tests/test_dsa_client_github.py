# -*- coding: utf-8 -*-
"""dsa_client.github_client 单元测试:mock requests.Session,不联网。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

import dsa_client.github_client as gc  # noqa: E402


class FakeResp:
    def __init__(self, status=200, data=None, content=b""):
        self.status_code = status
        self.headers = {}
        self.content = content
        self._data = {} if data is None else data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _client(req_data, sleep=None):
    real_sleep = []
    session = mock.MagicMock()
    session.request = mock.Mock(side_effect=req_data)
    return gc.GitHubClient("pat", session_factory=lambda: session,
                           sleep=(sleep if sleep is not None else real_sleep.append)), session, real_sleep


class TestBackoff:
    def test_retries_429_then_success(self):
        calls = [FakeResp(429), FakeResp(200, {"ok": True})]
        client, session, sleeps = _client(calls)
        client.request("GET", "/user")
        assert session.request.call_count == 2
        assert len(sleeps) >= 1, "应在 429 后退避"

    def test_retries_5xx_then_gives_up(self):
        calls = [FakeResp(500)] * 5
        client, session, sleeps = _client(calls)
        with pytest.raises(requests.HTTPError):
            client.request("GET", "/user")
        assert session.request.call_count == 5  # 1 + 重试上限 4


class TestRepoOps:
    def test_set_variable_uses_patch(self):
        client, session, _ = _client([FakeResp(201)])
        client.set_variable("alice", "dsa-cloud-alice", "STOCK_LIST", "600519,600036")
        method, url, kw = session.request.call_args.args[0], session.request.call_args.args[1], session.request.call_args.kwargs
        assert method == "PATCH"
        assert url.endswith("/repos/alice/dsa-cloud-alice/actions/variables/STOCK_LIST")
        assert kw["json"] == {"name": "STOCK_LIST", "value": "600519,600036"}

    def test_get_variable_none_on_404(self):
        client, session, _ = _client([FakeResp(404)])
        assert client.get_variable("alice", "repo", "STOCK_LIST") is None

    def test_dispatch_inputs(self):
        client, session, _ = _client([FakeResp(204, {})])
        client.dispatch("alice", "repo", ref="main", inputs={"mode": "stocks-only"})
        url = session.request.call_args.args[1]
        assert url.endswith("/actions/workflows/00-daily-analysis.yml/dispatches")
        assert session.request.call_args.kwargs["json"] == {"ref": "main", "inputs": {"mode": "stocks-only"}}


class TestEmptyBody:
    def test_204_empty_body_returns_none(self):
        # GitHub 对 dispatch POST / variables PATCH 返回 204 空 body,不能抛 JSONDecodeError
        client, session, _ = _client([FakeResp(204)])
        assert client.request("POST", "/actions/workflows/00-daily-analysis.yml/dispatches") is None


class TestIsRunning:
    def test_in_progress_true(self):
        assert gc.is_running([{"status": "in_progress"}]) is True

    def test_all_completed_false(self):
        runs = [{"status": "completed"}, {"status": "completed"}]
        assert gc.is_running(runs) is False