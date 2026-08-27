# -*- coding: utf-8 -*-
"""本地运行态与花费累加器:跨重启持久化,支撑可观测性与成本护栏。"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, date
from pathlib import Path

from . import config as cfg_mod

logger = logging.getLogger(__name__)

CONFIG_DIR = cfg_mod.CONFIG_DIR

# 经验值:单只股票一次完整分析的平均 LLM 花费(美元),用于触发前预估。
AVG_COST_PER_STOCK_USD = 0.02
# 超过该时长未成功运行即判定为"过期",前端红灯。
STALE_THRESHOLD_SECONDS = 48 * 3600


def _run_state_path() -> Path:
    return CONFIG_DIR / "run_state.json"


def _spend_path() -> Path:
    return CONFIG_DIR / "spend.json"


def load_run_state() -> dict:
    try:
        return json.loads(_run_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_success_ts": 0, "last_success_run_id": 0,
                "last_failure_ts": 0, "last_failure_run_id": 0, "last_checked_ts": 0}


def save_run_state(state: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _run_state_path().write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def record_run_outcome(state: dict, run: dict) -> None:
    """根据一次 workflow run 更新本地运行态。"""
    conclusion = run.get("conclusion")
    status = run.get("status")
    run_id = run.get("id", 0)
    ts = int(time.time())
    state["last_checked_ts"] = ts
    if status == "completed":
        if conclusion == "success":
            state["last_success_ts"] = ts
            state["last_success_run_id"] = run_id
        elif conclusion == "failure":
            state["last_failure_ts"] = ts
            state["last_failure_run_id"] = run_id


def is_stale(state: dict) -> bool:
    last = state.get("last_success_ts", 0)
    if not last:
        return True
    return (time.time() - last) > STALE_THRESHOLD_SECONDS


def load_spend() -> dict:
    try:
        return json.loads(_spend_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"date": "", "total_usd": 0.0}


def today_spend() -> float:
    s = load_spend()
    if s.get("date") != date.today().isoformat():
        return 0.0
    return float(s.get("total_usd", 0.0))


def add_spend(usd: float, config: object = None) -> float:
    s = load_spend()
    if s.get("date") != date.today().isoformat():
        s = {"date": date.today().isoformat(), "total_usd": 0.0}
    s["total_usd"] = round(float(s.get("total_usd", 0.0)) + usd, 4)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _spend_path().write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    if config is not None:
        refresh_budget_flag(config)
    return float(s["total_usd"])


def estimate_cost(num_stocks: int) -> float:
    return round(max(1, num_stocks) * AVG_COST_PER_STOCK_USD, 4)


def evaluate_budget(config: object) -> dict:
    """汇总当日预计花费与预算,返回比较结果(非阻塞,只用于可见性与告警)。"""
    try:
        budget = float(getattr(config, "budget_daily_usd", 0.0) or 0.0)
        mode = getattr(config, "budget_mode", "warn") or "warn"
    except Exception:
        budget, mode = 0.0, "warn"
    spent = today_spend()
    over = budget > 0 and spent > budget
    ratio = round(spent / budget, 3) if budget > 0 else 0.0
    return {
        "today_spend_usd": round(spent, 4),
        "budget_daily_usd": round(budget, 4),
        "budget_mode": mode,
        "over": bool(over),
        "ratio": ratio,
    }


def refresh_budget_flag(config: object) -> dict:
    """根据当前花费刷新运行态中的超额标记,并在超额时打印醒目告警(不中止任务)。"""
    info = evaluate_budget(config)
    state = load_run_state()
    changed = state.get("budget_over") != info["over"]
    state["budget_over"] = info["over"]
    if info["over"]:
        state["budget_over_since"] = state.get("budget_over_since") or int(time.time())
        logger.warning(
            "预算告警: 今日预计花费 $%.2f 已超过每日预算 $%.2f (mode=%s, 使用率 %.0f%%),"
            " 任务将继续运行但请关注成本。",
            info["today_spend_usd"], info["budget_daily_usd"], info["budget_mode"],
            (info["ratio"] * 100),
        )
    else:
        state.pop("budget_over_since", None)
    if changed:
        save_run_state(state)
    return info
