# -*- coding: utf-8 -*-
"""
策略信号章节渲染桥（Strategy Signal Section Bridge）

把 strategy_signal_probe 的确定性信号 JSON 渲染成日报中的
「🧭 策略信号」章节 Markdown。

- 开关：环境变量 STRATEGY_SIGNALS_ENABLED（默认关闭，false）
  - 开启时 generate_daily_report 在「摘要」之后、「个股详情」之前插入章节
  - probe 任何失败（配置损坏/超时/单股数据不足）均降级为 None，不抛异常
- 与 LLM 分析完全解耦：信号由 5 个规则策略产生，可复现、无随机性
"""

from __future__ import annotations

import logging
import os
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_ENABLED_SENTINEL = ("1", "true", "yes", "on")

_LEVEL_EMOJI = {"bullish": "🟢", "neutral": "🟡", "bearish": "🔴"}
_LEVEL_LABEL = {"bullish": "偏多", "neutral": "中性", "bearish": "偏空"}


def signals_enabled() -> bool:
    """读 STRATEGY_SIGNALS_ENABLED 开关（默认 false）。"""
    return os.getenv("STRATEGY_SIGNALS_ENABLED", "false").strip().lower() in _ENABLED_SENTINEL


def render_strategy_signal_section(results: List[Any]) -> Optional[str]:
    """运行 probe 并渲染「🧭 策略信号」章节 Markdown。

    失败一律返回 None（不打断日报主流程）。
    """
    if not signals_enabled():
        return None
    try:
        import asyncio

        from src.strategy_signal_probe import run_probe

        payload = asyncio.run(run_probe())
    except Exception as exc:  # noqa: BLE001 - optional section must never break the report
        logger.warning("[signals] probe skipped: %s", exc)
        return None

    symbols = payload.get("symbols") or {}
    if not symbols:
        return None

    lines: List[str] = [
        "## 🧭 策略信号",
        "",
        "> 基于规则策略的确定性信号（与 LLM 分析相互独立）。",
        "",
    ]
    for code, entry in symbols.items():
        name = entry.get("name") or code
        as_of = entry.get("as_of_date") or payload.get("as_of_date", "")
        lines.append(f"### {_escape_md(name)} ({code}) — {as_of}")
        lines.append("")
        for group_name, group in (entry.get("groups") or {}).items():
            level = group.get("level", "neutral")
            emoji = _LEVEL_EMOJI.get(level, "⚪")
            label = _LEVEL_LABEL.get(level, "未知")
            ratio = group.get("vote_ratio", 0.0)
            triggered = group.get("triggered", 0)
            total = group.get("total", 0)
            lines.append(
                f"- **{group_name}组**：{emoji}{label} "
                f"（{triggered}/{total} 触发，ratio {ratio:.0%}）"
            )
            for sid, sig in (group.get("signals") or {}).items():
                mark = "✅" if sig.get("triggered") else "—"
                reason = sig.get("reason") or ""
                price = sig.get("entry_price")
                price_txt = f"，参考价 {price}" if price else ""
                lines.append(f"  - {mark} `{sid}` {reason}{price_txt}")
        lines.append("")

    issues = payload.get("data_issues") or {}
    if issues:
        lines.append(f"*⚠️ 数据不足：{len(issues)} 只股票未参与信号计算*")
    return "\n".join(lines)


def _escape_md(name: str) -> str:
    return name.replace("*", r"\*") if name else name