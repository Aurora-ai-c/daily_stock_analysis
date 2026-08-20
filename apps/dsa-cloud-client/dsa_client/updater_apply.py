# -*- coding: utf-8 -*-
"""更新应用纯逻辑:校验/备份规划(由 updater.exe 调用)。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def verify_sha256(path: str, expected: str) -> bool:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest() == expected.lower()


_VERSION_RE = re.compile(r"^(\d+\.\d+\.\d+)_")


def plan_backup(backup_dir: str, keep: int = 3) -> list[str]:
    """返回应删除的旧备份路径(LRU:按文件名内嵌版本号升序)。"""
    files = list(Path(backup_dir).glob("*.bak"))
    keyed = []
    for f in files:
        m = _VERSION_RE.match(f.name)
        if m:
            keyed.append((tuple(int(x) for x in m.group(1).split(".")), str(f)))
    keyed.sort(key=lambda kv: kv[0])
    return [path for _, path in keyed[:-keep]] if len(keyed) > keep else []