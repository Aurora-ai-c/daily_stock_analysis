# -*- coding: utf-8 -*-
"""版本读取:优先 frozen 打包的 _version.py,fallback 源码文件,最终 dev。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

VERSION_FILE = "dsa_client/_version.py"
VERSION_MODULE_PATH = str(Path(__file__).resolve().parent / "_version.py")


def _extract(path: str) -> str | None:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
        if text.startswith("__version__ = "):
            return text.split("=", 1)[1].strip().strip('"')
    except OSError:
        return None
    return None


def get_version() -> str:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            frozen = Path(meipass) / "_version.py"
            if frozen.exists():
                found = _extract(str(frozen))
                if found:
                    return found
    found = _extract(VERSION_MODULE_PATH)
    if found:
        return found
    return "0.0.0-dev"
