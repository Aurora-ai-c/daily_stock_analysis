# -*- coding: utf-8 -*-
"""本地配置存取:目录、DPAPI 加密 PAT、随机访问 token。"""

from __future__ import annotations

import base64
import json
import secrets
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".dsa-cloud"


def config_path() -> Path:
    return CONFIG_DIR / "config.json"


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def dpapi_encrypt(plaintext: str) -> str:
    if not sys.platform.startswith("win"):
        raise NotImplementedError("DPAPI 仅支持 Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def _make_blob(data: bytes) -> DATA_BLOB:
        buf = (ctypes.c_byte * len(data)).from_buffer_copy(data)
        return DATA_BLOB(len(data), buf)

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1
    crypt = ctypes.windll.crypt32
    crypt.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt.CryptProtectData.restype = wintypes.BOOL

    data_in = _make_blob(plaintext.encode("utf-8"))
    data_out = DATA_BLOB()
    ok = crypt.CryptProtectData(
        ctypes.byref(data_in), "dsa-cloud-pat", None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out),
    )
    if not ok:
        raise OSError("CryptProtectData failed")
    try:
        raw = ctypes.string_at(data_out.pbData, data_out.cbData)
        return base64.b64encode(raw).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(data_out.pbData)


def dpapi_decrypt(b64: str) -> str:
    if not sys.platform.startswith("win"):
        raise NotImplementedError("DPAPI 仅支持 Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def _make_blob(data: bytes) -> DATA_BLOB:
        buf = (ctypes.c_byte * len(data)).from_buffer_copy(data)
        return DATA_BLOB(len(data), buf)

    crypt = ctypes.windll.crypt32
    crypt.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt.CryptUnprotectData.restype = wintypes.BOOL

    data_in = _make_blob(base64.b64decode(b64))
    data_out = DATA_BLOB()
    ok = crypt.CryptUnprotectData(
        ctypes.byref(data_in), None, None, None, None, 0, ctypes.byref(data_out),
    )
    if not ok:
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(data_out.pbData)


class Config:
    def __init__(self) -> None:
        self.owner: str = ""
        self.repo: str = ""
        self.token: str = ""
        self.pat_enc: str = ""

    def set_pat(self, plaintext: str) -> None:
        self.pat_enc = dpapi_encrypt(plaintext)

    def get_pat(self) -> str:
        """未配置或 DPAPI 解密失败(跨用户/机器迁移、blob 损坏/非 base64)时返回空串。"""
        if not self.pat_enc:
            return ""
        try:
            return dpapi_decrypt(self.pat_enc)
        except (OSError, ValueError):
            # OSError: CryptUnprotectData 失败;ValueError(binascii.Error 等): blob 非 base64 或解密结果非 UTF-8
            return ""

    def validate(self) -> list[str]:
        missing = []
        if not self.owner:
            missing.append("owner")
        if not self.repo:
            missing.append("repo")
        if not self.pat_enc:
            missing.append("pat")
        return missing

    def to_dict(self) -> dict:
        return {"owner": self.owner, "repo": self.repo, "token": self.token, "pat_enc": self.pat_enc}

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        c = cls()
        c.owner = data.get("owner", "")
        c.repo = data.get("repo", "")
        c.token = data.get("token", "")
        c.pat_enc = data.get("pat_enc", "")
        return c

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config_path().write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "Config":
        return cls.from_dict(json.loads(config_path().read_text(encoding="utf-8")))


def initialize_config() -> Config:
    if config_path().exists():
        return Config.load()
    c = Config()
    c.token = generate_token()
    c.save()
    return c
