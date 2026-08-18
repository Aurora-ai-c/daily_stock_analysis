# -*- coding: utf-8 -*-
"""Shared test helper to keep litellm imports lightweight in unit tests."""

import sys
import types


def ensure_litellm_stub() -> None:
    """Force-install a minimal litellm stub.

    Also removes any real litellm modules already imported, so callers get
    stub semantics regardless of test collection order. Tests that need real
    litellm types must call remove_litellm_stub() first and re-install the
    stub afterwards; importing real litellm also loads the developer's
    .env via dotenv, so those tests must restore os.environ too (see
    test_llm_usage.py for the full pattern).
    """
    existing = sys.modules.get("litellm")
    if getattr(existing, "__dsa_test_stub__", False):
        return

    for module_name in ("litellm.types.utils", "litellm.types", "litellm"):
        sys.modules.pop(module_name, None)

    litellm_stub = types.ModuleType("litellm")
    litellm_stub.__dsa_test_stub__ = True

    class _DummyRouter:  # pragma: no cover
        pass

    class _DummyRateLimitError(Exception):
        pass

    class _DummyContextWindowExceededError(Exception):
        pass

    class _DummyUsage:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def model_dump(self):
            return dict(self.__dict__)

        def dict(self):
            return dict(self.__dict__)

    litellm_types_stub = types.ModuleType("litellm.types")
    litellm_types_utils_stub = types.ModuleType("litellm.types.utils")
    litellm_types_utils_stub.Usage = _DummyUsage
    litellm_types_stub.utils = litellm_types_utils_stub

    litellm_stub.Router = _DummyRouter
    litellm_stub.RateLimitError = _DummyRateLimitError
    litellm_stub.ContextWindowExceededError = _DummyContextWindowExceededError
    litellm_stub.completion = lambda **kwargs: None
    litellm_stub.types = litellm_types_stub
    sys.modules["litellm"] = litellm_stub
    sys.modules["litellm.types"] = litellm_types_stub
    sys.modules["litellm.types.utils"] = litellm_types_utils_stub


def remove_litellm_stub() -> None:
    """Remove this stub so tests that need real LiteLLM types can import them."""
    if not getattr(sys.modules.get("litellm"), "__dsa_test_stub__", False):
        return

    for module_name in ("litellm.types.utils", "litellm.types", "litellm"):
        sys.modules.pop(module_name, None)
