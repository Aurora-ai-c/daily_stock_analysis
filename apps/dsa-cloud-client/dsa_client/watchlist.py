# -*- coding: utf-8 -*-
"""自选股解析与校验:字符串 <-> 结构化列表。

代码形态约定(与云端 strategy_signal_probe / 客户端 placeholder 一致):
  A 股 6 位数字(600519) · 港股 4-5 位数字(00700) · 美股 US. 前缀(US.AAPL)
  兼容带前缀形态 sh600519 / sz000001 / hk00700。
"""

from __future__ import annotations

import re

_US = re.compile(r"^US\.[A-Za-z][A-Za-z.\-]{0,9}$")
_SIX_DIGIT = re.compile(r"^\d{6}$")
_HK_BARE = re.compile(r"^\d{4,5}$")
_PREFIXED = re.compile(r"^(sh|sz|bj|hk)\d{1,6}$", re.IGNORECASE)


def split_symbols(raw: str | None) -> list[str]:
    """逗号/中文逗号/空白分隔 → 去空 token 列表(保序)。"""
    if not raw:
        return []
    tokens = re.split(r"[,，\s]+", str(raw))
    return [t.strip() for t in tokens if t.strip()]


def normalize_symbol(token: str) -> str:
    """归一:US 前缀部分大小写规范;其余保留原样(不强行加交易所前缀)。"""
    t = (token or "").strip()
    if t.upper().startswith("US."):
        return "US." + t[3:].strip().upper()
    return t


def validate_symbol(token: str) -> str | None:
    """返回错误信息;None 表示形态可信。

    宽松策略:无法识别时返回提示,由调用方决定拒绝还是降级为警告。
    """
    t = (token or "").strip()
    if not t:
        return "代码不能为空"
    if _US.match(t) or _SIX_DIGIT.match(t) or _HK_BARE.match(t) or _PREFIXED.match(t):
        return None
    if t.upper().startswith(("SH", "SZ", "BJ", "HK")) and t[2:].isdigit():
        return None
    return f"无法识别的代码形态: {t}(支持 600519 / 00700 / sh600519 / US.AAPL)"


def parse_watchlist(raw: str | None) -> dict:
    """解析为 {"items", "invalid", "duplicates"}:去重保序,非法代码单列。"""
    items: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    invalid: list[str] = []
    for token in split_symbols(raw):
        norm = normalize_symbol(token)
        err = validate_symbol(norm)
        if err:
            invalid.append(norm)
            continue
        key = norm.upper()
        if key in seen:
            duplicates.append(norm)
            continue
        seen.add(key)
        items.append(norm)
    return {"items": items, "invalid": invalid, "duplicates": duplicates}


def join_symbols(items: list[str]) -> str:
    return ",".join(items)
