# -*- coding: utf-8 -*-
"""deploy_user.py 单元测试:mock requests,不联网。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest
import requests

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

    def test_real_422_via_session_is_idempotent(self):
        api = _mk_api()
        resp = requests.Response()
        resp.status_code = 422
        resp._content = b'{"message": "Repository creation failed."}'
        resp.headers["Content-Type"] = "application/json"
        with mock.patch.object(api.session, "request", return_value=resp) as sess_req:
            assert deploy_user.generate_repo(api, "tpl-owner", "tpl-repo", "alice", "dsa-cloud-alice", dry_run=False) == "exists"
        sess_req.assert_called_once()


class TestEnableActions:
    def test_puts_enabled(self):
        api = _mk_api()
        with mock.patch.object(api, "request", return_value=FakeResponse(204)) as req:
            deploy_user.enable_actions(api, "alice", "dsa-cloud-alice", dry_run=False)
        req.assert_called_once_with(
            "PUT", "/repos/alice/dsa-cloud-alice/actions/permissions",
            json={"enabled": True},
        )