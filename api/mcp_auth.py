# -*- coding: utf-8 -*-
"""MCP 多 key 鉴权与令牌桶限流。"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Optional


class McpAuthConfigError(ValueError):
    pass


DEFAULT_SCOPE = {"read:basic"}


def is_mcp_enabled() -> bool:
    return bool(os.getenv("MCP_API_KEYS", "").strip())


def load_keys() -> dict[str, set[str]]:
    raw = os.getenv("MCP_API_KEYS", "")
    keys: dict[str, set[str]] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise McpAuthConfigError(f"invalid MCP_API_KEYS entry: {entry!r}")
        key_id, digest = entry.split(":", 1)
        key_id = key_id.strip().lower()
        digest = digest.strip()
        if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            raise McpAuthConfigError(f"invalid sha256 digest for key {key_id!r}")
        scope_raw = os.getenv(f"MCP_KEY_{key_id.upper()}_SCOPE", "").strip()
        scope = {s.strip() for s in scope_raw.split(",") if s.strip()} or set(DEFAULT_SCOPE)
        keys[key_id] = scope
    return keys


def _match(key_id: str, digest: str, plain: str) -> bool:
    return hmac_compare(digest, hashlib.sha256(plain.encode()).hexdigest())


def hmac_compare(a: str, b: str) -> bool:
    return hashlib.sha256(a.encode()).digest() == hashlib.sha256(b.encode()).digest()


def authenticate(header_value: Optional[str]) -> Optional[str]:
    if not header_value or not header_value.startswith("Bearer "):
        return None
    plain = header_value[len("Bearer "):].strip()
    for key_id, digest in _raw_entries():
        if _match(key_id, digest, plain):
            return key_id
    return None


def _raw_entries() -> list[tuple[str, str]]:
    out = []
    for entry in os.getenv("MCP_API_KEYS", "").split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        key_id, digest = entry.split(":", 1)
        out.append((key_id.strip().lower(), digest.strip()))
    return out


def scope_for(key_id: str) -> set[str]:
    return load_keys().get(key_id, set(DEFAULT_SCOPE))


class RateLimiter:
    """令牌桶:rate 个/秒,容量 capacity。"""

    def __init__(self, rate: float, capacity: float):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False
