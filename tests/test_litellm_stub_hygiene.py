# -*- coding: utf-8 -*-
"""Regression: importing test_llm_usage must not pollute other tests.

test_llm_usage imports the real litellm at module level (remove_litellm_stub()
+ `from litellm.types.utils import Usage`). Two kinds of pollution follow:
  1. sys.modules["litellm"] points at real litellm, so later modules that call
     ensure_litellm_stub() no-op and bind to real litellm.
  2. Importing real litellm MUTATES os.environ (e.g. sets LITELLM_MODEL),
     which leaks into every later test in the same process.
Both must be undone so tests collected after test_llm_usage behave the same
as when they run standalone (see test_system_config_service.py failures).
"""

import os
import sys

import tests.test_llm_usage  # noqa: F401  # module-level import triggers the pollution

from tests.litellm_stub import ensure_litellm_stub


def test_llm_usage_import_leaves_stub_installed() -> None:
    ensure_litellm_stub()
    assert getattr(sys.modules.get("litellm"), "__dsa_test_stub__", False) is True


def test_llm_usage_import_does_not_leak_env() -> None:
    assert os.environ.get("LITELLM_MODEL") in (None, "")
    assert os.environ.get("LITELLM_MODEL") != "deepseek/deepseek-v4-flash"
