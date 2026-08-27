# -*- coding: utf-8 -*-
"""Offline test for client-side data-source health extraction from artifacts."""
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

import dsa_client.server as server  # noqa: E402
import dsa_client.github_client as gc  # noqa: E402


class _FakeGH:
    def __init__(self, *a, **k):
        pass

    def list_artifacts(self, owner, repo, per_page=10):
        return [
            {"id": 2, "name": "analysis-reports-2", "expired": False},
            {"id": 1, "name": "analysis-reports-1", "expired": False},
        ]

    def download_artifact(self, owner, repo, artifact_id):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            payload = {"summary": "all_failed", "sources": [{"name": "EfinanceFetcher", "state": "open", "priority": 0}], "total": 1}
            zf.writestr("data_source_health.json", json.dumps(payload))
        return buf.getvalue()


class _Cfg:
    owner = "o"
    repo = "r"

    def get_pat(self):
        return "tok"

    github_proxy = ""
    github_ca_bundle = ""


def test_fetch_data_source_health_extracts(monkeypatch):
    monkeypatch.setattr(gc, "GitHubClient", _FakeGH)
    health = server.fetch_data_source_health(_Cfg())
    assert health is not None
    assert health["summary"] == "all_failed"
    assert health["sources"][0]["name"] == "EfinanceFetcher"


def test_fetch_data_source_health_no_creds():
    class _NoCred:
        owner = None
        repo = None
        def get_pat(self):
            return None
    assert server.fetch_data_source_health(_NoCred()) is None
