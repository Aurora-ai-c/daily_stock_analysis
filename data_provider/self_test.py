# -*- coding: utf-8 -*-
"""Data source self-test / diagnostics CLI.

Offline by default: reports configured sources, priorities, missing env,
and circuit-breaker state.

Optional live probe (attempts a short real fetch for a sample symbol):

    python -m data_provider.self_test --probe
"""

from __future__ import annotations

import argparse
import sys
import threading
import traceback
from typing import Any, Dict, List, Optional, Tuple


def _run_with_timeout(func, timeout: float) -> Tuple[bool, Any]:
    """Run func in a daemon thread; return (ok, result_or_exception)."""
    box: Dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = func()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False, TimeoutError(f"timed out after {timeout}s")
    if "error" in box:
        return False, box["error"]
    return True, box.get("result")


def _report_config() -> List[Dict[str, Any]]:
    import os

    from data_provider.registry import discover_fetchers

    rows: List[Dict[str, Any]] = []
    try:
        specs = discover_fetchers()
    except Exception as exc:  # noqa: BLE001
        print(f"[error] fetcher discovery failed: {exc}")
        return rows
    for spec in specs:
        rows.append(
            {
                "name": getattr(spec, "name", "?"),
                "priority": getattr(spec, "priority", 0),
                "enabled": getattr(spec, "enabled", False),
                "module": getattr(spec, "module", ""),
                "env_required": getattr(spec, "env_required", []) or [],
                "missing_env": [
                    k for k in (getattr(spec, "env_required", []) or []) if not os.environ.get(k)
                ],
            }
        )
    return rows


def _report_breakers() -> Dict[str, Dict[str, str]]:
    from data_provider.realtime_types import (
        get_chip_circuit_breaker,
        get_realtime_circuit_breaker,
    )

    out: Dict[str, Dict[str, str]] = {}
    try:
        out["realtime"] = get_realtime_circuit_breaker().get_status()
    except Exception:  # noqa: BLE001
        out["realtime"] = {}
    try:
        out["chip"] = get_chip_circuit_breaker().get_status()
    except Exception:  # noqa: BLE001
        out["chip"] = {}
    return out


def _probe(symbol: str, days: int, timeout: float) -> Dict[str, Any]:
    from data_provider.base import DataFetcherManager

    def _build_and_fetch() -> Dict[str, Any]:
        mgr = DataFetcherManager()
        df, src = mgr.get_daily_data(symbol, days=days)
        return {"rows": len(df) if df is not None else 0, "source": src}

    ok, res = _run_with_timeout(_build_and_fetch, timeout)
    if ok:
        return {"ok": True, "detail": res}
    return {"ok": False, "detail": repr(res)}


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Data source self-test / diagnostics")
    p.add_argument("--probe", action="store_true", help="attempt a short live fetch")
    p.add_argument("--symbol", default="600519", help="sample symbol for probe")
    p.add_argument("--days", type=int, default=5)
    p.add_argument("--timeout", type=float, default=20.0, help="per-probe timeout (s)")
    args = p.parse_args(argv)

    print("=== Data source configuration ===")
    rows = _report_config()
    if not rows:
        print("  (no fetchers discovered)")
    for r in sorted(rows, key=lambda x: (not x["enabled"], -x["priority"])):
        flag = "ON " if r["enabled"] else "off"
        missing = ",".join(r["missing_env"]) or "-"
        print(f"  [{flag}] {r['name']:22} prio={r['priority']:>3} missing_env={missing}")

    print("\n=== Circuit breakers ===")
    breakers = _report_breakers()
    for bname, states in breakers.items():
        if not states:
            print(f"  {bname}: (no recorded state)")
        for src, st in states.items():
            print(f"  {bname}: {src} -> {st}")

    if args.probe:
        print(
            f"\n=== Live probe (symbol={args.symbol}, days={args.days}, "
            f"timeout={args.timeout}s) ==="
        )
        try:
            res = _probe(args.symbol, args.days, args.timeout)
            if res["ok"]:
                d = res["detail"]
                print(f"  OK: source={d['source']} rows={d['rows']}")
            else:
                print(f"  FAILED: {res['detail']}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {exc}\n{traceback.format_exc()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
