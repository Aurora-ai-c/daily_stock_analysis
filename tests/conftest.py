# -*- coding: utf-8 -*-
"""Test layering: keep the offline gate fast and deterministic.

CI already runs ``pytest -m "not network"`` (see scripts/ci_gate.sh). This
conftest adds a safety net for *local* runs (which may omit the ``-m`` flag):
any test marked ``@pytest.mark.network`` is skipped unless the caller opts in
via ``DSA_RUN_ONLINE=1``.
"""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("DSA_RUN_ONLINE") == "1":
        return
    skip_online = pytest.mark.skip(
        reason="requires network/cloud (set DSA_RUN_ONLINE=1 to run)"
    )
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_online)
