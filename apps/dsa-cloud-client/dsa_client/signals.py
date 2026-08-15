# -*- coding: utf-8 -*-
"""策略信号解析:把 artifacts 中的 strategy_signals_latest.json 归一为信号卡。"""

from __future__ import annotations

from dataclasses import dataclass, asdict

SIGNAL_FIELDS = (
    "symbol", "as_of_date", "strategy", "action", "entry_price",
    "stop_loss", "target_price", "confidence", "supports", "conflicts",
)


@dataclass
class SignalCard:
    symbol: str | None = None
    as_of_date: str | None = None
    strategy: str | None = None
    action: str | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    target_price: float | None = None
    confidence: float | None = None
    supports: list | None = None
    conflicts: list | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def parse_signal(record: dict) -> SignalCard:
    vals = {f: record.get(f) for f in SIGNAL_FIELDS}
    return SignalCard(**vals)


def _is_record(item) -> bool:
    return isinstance(item, dict) and "symbol" in item


def extract_cards(aggregate) -> list[SignalCard]:
    if isinstance(aggregate, list):
        return [parse_signal(r) for r in aggregate if _is_record(r)]
    if not isinstance(aggregate, dict):
        return []
    if "signals" in aggregate and isinstance(aggregate["signals"], list):
        return [parse_signal(r) for r in aggregate["signals"] if _is_record(r)]
    if "symbols" in aggregate and isinstance(aggregate["symbols"], dict):
        return _extract_from_symbols(aggregate)
    source = aggregate.get("per_symbol", aggregate)
    cards: list[SignalCard] = []
    for val in source.values():
        if _is_record(val):
            cards.append(parse_signal(val))
    return cards


def _extract_from_symbols(aggregate: dict) -> list[SignalCard]:
    """真实产物形状（src/strategy_signal_probe.py aggregate()）:
    {"symbols": {code: {"as_of_date": ..., "groups": {group: {"signals": {sid: {...}}}}}}}。
    code 注入为记录的 symbol;组内无信号则整组跳过。
    """
    symbols = aggregate.get("symbols") or {}
    default_date = aggregate.get("as_of_date")
    cards: list[SignalCard] = []
    for code, entry in symbols.items():
        if not isinstance(entry, dict):
            continue
        as_of = entry.get("as_of_date") or default_date
        for group in (entry.get("groups") or {}).values():
            if not isinstance(group, dict):
                continue
            signals = group.get("signals") or {}
            if not signals:
                continue
            for sid, sig in signals.items():
                if not isinstance(sig, dict):
                    continue
                cards.append(parse_signal({
                    "symbol": code,
                    "as_of_date": as_of,
                    "strategy": sid,
                    "entry_price": sig.get("entry_price"),
                }))
    return cards
