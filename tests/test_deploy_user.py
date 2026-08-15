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
        puts = [c.args[1] for c in api.request.call_args_list if c.args[0] == "PUT"]
        assert puts == ["/repos/alice/dsa-cloud-alice/actions/secrets/WEBHOOK_URL"]

    def test_overwrite_writes_all(self):
        api = self._api()
        with mock.patch.object(api, "request", side_effect=[
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

    def test_real_encryption_via_session(self):
        import base64
        api = self._api()
        pubkey_b64 = base64.b64encode(bytes(range(32))).decode("ascii")

        def _resp(status, body):
            r = requests.Response()
            r.status_code = status
            r._content = body
            r.headers["Content-Type"] = "application/json"
            return r

        with mock.patch.object(api.session, "request", side_effect=[
            _resp(200, b'{"secrets": []}'),
            _resp(200, ('{"key": "%s", "key_id": "1"}' % pubkey_b64).encode("ascii")),
            _resp(204, b"{}"),
        ]) as sess_req:
            written = deploy_user.write_secrets(api, "alice", "dsa-cloud-alice",
                                                {"WEBHOOK_URL": "u"}, overwrite=False, dry_run=False)
        assert written == ["WEBHOOK_URL"]
        put_call = [c for c in sess_req.call_args_list if c.args[0] == "PUT"][0]
        payload = put_call.kwargs["json"]
        assert payload["key_id"] == "1"
        assert payload["encrypted_value"]


class TestSetVariable:
    def test_patch_variable(self):
        api = _mk_api()
        with mock.patch.object(api, "request", return_value=FakeResponse(201)) as req:
            deploy_user.set_variable(api, "alice", "dsa-cloud-alice", "STOCK_LIST", "600519,600036", dry_run=False)
        req.assert_called_once_with(
            "PATCH", "/repos/alice/dsa-cloud-alice/actions/variables/STOCK_LIST",
            json={"name": "STOCK_LIST", "value": "600519,600036"},
        )


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