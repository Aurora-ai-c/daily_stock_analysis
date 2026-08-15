# -*- coding: utf-8 -*-
"""dsa_client.signals 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

import dsa_client.signals as sig  # noqa: E402


def test_parse_signal_blank_missing():
    card = sig.parse_signal({"symbol": "600519", "strategy": "ma_crossover_v1"})
    assert card.symbol == "600519"
    assert card.entry_price is None
    assert card.action is None


def test_parse_signal_all_fields():
    rec = {"symbol": "600519", "as_of_date": "2026-08-14", "strategy": "rsi_reversion_v1",
           "action": "buy", "entry_price": 1500.0, "stop_loss": 1440.0,
           "target_price": 1650.0, "confidence": 0.7, "supports": ["a"], "conflicts": []}
    card = sig.parse_signal(rec)
    assert card.to_dict() == rec


def test_extract_from_list():
    cards = sig.extract_cards([{"symbol": "a"}, {"symbol": "b"}])
    assert [c.symbol for c in cards] == ["a", "b"]


def test_extract_from_per_symbol():
    aggregate = {"per_symbol": {"600519": {"symbol": "600519", "confidence": 0.5}}}
    cards = sig.extract_cards(aggregate)
    assert cards[0].symbol == "600519"