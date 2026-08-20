# -*- coding: utf-8 -*-
"""启动期更新检查:后台 daemon 线程,失败静默。"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from dsa_client.updater import check_for_update

# Part-B 审计记录(frozen 打包或非仓库根运行时 src 不在 sys.path,失败则跳过审计)
try:
    from src.services.run_diagnostics import UpdateEventDiagnostic
except ImportError:  # pragma: no cover - frozen exe / 未以仓库根为 cwd
    UpdateEventDiagnostic = None  # type: ignore[assignment]

# 与 updater.py 的 data/update_check_cache.json 同目录约定
AUDIT_FILE = Path("data/update_audit.json")


def _append_audit(rec) -> None:
    """审计落盘:data/update_audit.json 追加 JSON 行;目录不存在时创建;写失败不抛。"""
    try:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec.sanitize(), ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _run_check(cache_file: Optional[str]):
    try:
        result = check_for_update(cache_file=cache_file)
        if result.error:
            return
        if UpdateEventDiagnostic is not None:
            rec = UpdateEventDiagnostic(
                version=result.latest, event="available" if result.update_available else "up_to_date",
                status="ok", detail=result.notes)
            _append_audit(rec)
        if result.update_available:
            print(f"[updater] 发现新版本 {result.latest},后台已准备更新。")
    except Exception:  # noqa: BLE001
        pass  # 更新检查失败不影响启动


def start_update_check_thread(cache_file: Optional[str] = None) -> threading.Thread:
    thread = threading.Thread(target=_run_check, args=(cache_file,), daemon=True)
    thread.start()
    return thread