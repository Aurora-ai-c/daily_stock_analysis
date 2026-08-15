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