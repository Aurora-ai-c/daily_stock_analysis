# -*- coding: utf-8 -*-
"""MCP 鉴权与限流模块测试。"""
import hashlib
import pytest
from api.mcp_auth import load_keys, authenticate, RateLimiter, is_mcp_enabled


def _hash(s):
    return hashlib.sha256(s.encode()).hexdigest()


class TestLoadKeys:
    def test_parses_env(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEYS", f"alice:{_hash('k1')},bob:{_hash('k2')}")
        monkeypatch.setenv("MCP_KEY_ALICE_SCOPE", "read:basic,read:sensitive")
        keys = load_keys()
        assert "alice" in keys and "read:sensitive" in keys["alice"]
        assert keys["bob"] == {"read:basic"}  # 未配置 scope 默认

    def test_disabled_when_unset(self, monkeypatch):
        monkeypatch.delenv("MCP_API_KEYS", raising=False)
        assert is_mcp_enabled() is False

    def test_invalid_format_raises(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEYS", "alice:nothex")
        with pytest.raises(Exception):
            load_keys()


class TestAuthenticate:
    def test_valid_key_returns_id(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEYS", f"alice:{_hash('secret1')}")
        assert authenticate(f"Bearer secret1") == "alice"

    def test_invalid_key_returns_none(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEYS", f"alice:{_hash('secret1')}")
        assert authenticate("Bearer wrong") is None

    def test_missing_header_returns_none(self, monkeypatch):
        monkeypatch.setenv("MCP_API_KEYS", f"alice:{_hash('secret1')}")
        assert authenticate(None) is None


class TestRateLimiter:
    def test_allows_up_to_capacity(self):
        rl = RateLimiter(rate=10.0, capacity=2.0)
        assert rl.allow() and rl.allow()
        assert not rl.allow()

    def test_refills(self):
        import time as _t
        rl = RateLimiter(rate=100.0, capacity=1.0)
        assert rl.allow()
        _t.sleep(0.03)
        assert rl.allow()
