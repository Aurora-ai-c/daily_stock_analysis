# -*- coding: utf-8 -*-
"""
策略信号章节渲染桥（Strategy Signal Section Bridge）

把 strategy_signal_probe 的确定性信号 JSON 渲染成日报中的
「🧭 策略信号」章节 Markdown。

- 开关：环境变量 STRATEGY_SIGNALS_ENABLED（默认开启，true）
  - 开启时 generate_daily_report 在「摘要」之后、「个股详情」之前插入章节
  - probe 任何失败（配置损坏/超时/单股数据不足）均降级为 None，不抛异常
  - 设 STRATEGY_SIGNALS_ENABLED=false 可关闭
- 与 LLM 分析完全解耦：信号由 5 个规则策略产生，可复现、无随机性
- 双轨对比：按股票把每个信号组的方向与 LLM 结论方向（sentiment_score）做
  位比对，输出 ✓ 支撑 / ⚠️ 未确认 / ⚠️ 分歧 标注；仅提示、不覆盖结论。
  LLM 结论无对应股票或无 score 时降级为不标注。
"""

from __future__ import annotations

import logging
import os
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_ENABLED_SENTINEL = ("1", "true", "yes", "on")

# bearish 语义：组内策略全部未触发（ratio == 0），即"无信号"，非"看空"。
# 标签统一为 ⚪ 未触发，避免与真正的空头观点混淆。
_LEVEL_EMOJI = {"bullish": "🟢", "neutral": "🟡", "bearish": "⚪"}
_LEVEL_LABEL = {"bullish": "偏多", "neutral": "中性", "bearish": "未触发"}

_LLM_BULL_MIN = 60  # sentiment_score > 60 视为偏多
_LLM_BEAR_MAX = 40  # sentiment_score < 40 视为偏空

_AGREE = "agree"
_NO_SIGNAL = "no_signal"
_CONFLICT = "conflict"

_SUPPORT_MARKS = {
    _AGREE: "✓ 机器信号支撑",
    _NO_SIGNAL: "⚠️ 机器信号未确认",
    _CONFLICT: "⚠️ 与机器信号方向分歧",
}


def _llm_direction(score: Optional[int]) -> Optional[str]:
    """把 LLM sentiment_score 映射为 bullish / bearish / None（中性）。"""
    if score is None:
        return None
    if score > _LLM_BULL_MIN:
        return "bullish"
    if score < _LLM_BEAR_MAX:
        return "bearish"
    return None


def judge_support(llm_score: Optional[int], signal_level: str) -> Optional[str]:
    """比对 LLM 结论方向与信号组方向的支撑度。

    返回 None（任一方中性/缺失，不标注）或 "agree" / "no_signal" / "conflict"。

    语义（当前策略集无真看空形态，bearish == 未触发）：
    -  LLM 偏多 + 组触发   -> agree（规则验证支撑）
    -  LLM 偏多 + 组未触发 -> no_signal（规则未给验证）
    -  LLM 偏空 + 组触发   -> conflict（规则方向与结论相反）
    -  LLM 偏空 + 组未触发 -> None（未触发不代表看空，无依据判定）
    -  任一方中性          -> None
    """
    llm_dir = _llm_direction(llm_score)
    if llm_dir is None:
        return None
    if signal_level == "bullish":
        return _AGREE if llm_dir == "bullish" else _CONFLICT
    if signal_level == "bearish":
        return _NO_SIGNAL if llm_dir == "bullish" else None
    return None


def signals_enabled() -> bool:
    """读 STRATEGY_SIGNALS_ENABLED 开关（默认 true；设 false 关闭）。"""
    return os.getenv("STRATEGY_SIGNALS_ENABLED", "true").strip().lower() in _ENABLED_SENTINEL


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

    by_code: dict = {}
    for r in results or []:
        code = getattr(r, "code", None)
        if code:
            by_code[code] = getattr(r, "sentiment_score", None)

    lines: List[str] = [
        "## 🧭 策略信号",
        "",
        "> 基于规则策略的确定性信号（与 LLM 分析相互独立）。",
        "> 方向与 LLM 结论按位比对：✓ 支撑 / ⚠️ 未确认 / ⚠️ 分歧，仅提示、不覆盖结论。",
        "",
    ]
    for code, entry in symbols.items():
        name = entry.get("name") or code
        as_of = entry.get("as_of_date") or payload.get("as_of_date", "")
        lines.append(f"### {_escape_md(name)} ({code}) — {as_of}")
        lines.append("")
        llm_score = by_code.get(code)
        for group_name, group in (entry.get("groups") or {}).items():
            level = group.get("level", "neutral")
            emoji = _LEVEL_EMOJI.get(level, "⚪")
            label = _LEVEL_LABEL.get(level, "未知")
            ratio = group.get("vote_ratio", 0.0)
            triggered = group.get("triggered", 0)
            total = group.get("total", 0)
            line = (
                f"- **{group_name}组**：{emoji}{label} "
                f"（{triggered}/{total} 触发，ratio {ratio:.0%}）"
            )
            mark = judge_support(llm_score, level)
            if mark:
                line += f" **{_SUPPORT_MARKS[mark]}**"
            lines.append(line)
            for sid, sig in (group.get("signals") or {}).items():
                mark_trigger = "✅" if sig.get("triggered") else "—"
                reason = sig.get("reason") or ""
                price = sig.get("entry_price")
                price_txt = f"，参考价 {price}" if price else ""
                lines.append(f"  - {mark_trigger} `{sid}` {reason}{price_txt}")
        lines.append("")

    issues = payload.get("data_issues") or {}
    if issues:
        lines.append(f"*⚠️ 数据不足：{len(issues)} 只股票未参与信号计算*")
    return "\n".join(lines)


def _escape_md(name: str) -> str:
    return name.replace("*", r"\*") if name else name