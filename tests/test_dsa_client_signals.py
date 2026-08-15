# -*- coding: utf-8 -*-
"""dsa_client.signals 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/dsa-cloud-client"))

import dsa_client.signals as sig  # noqa: E402
import pytest  # noqa: E402


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


def test_extract_from_signals_wrapped_list():
    aggregate = {"signals": [
        {"symbol": "600519", "action": "buy"},
        {"symbol": "000001", "action": "sell"},
    ]}
    cards = sig.extract_cards(aggregate)
    assert [c.symbol for c in cards] == ["600519", "000001"]
    assert [c.action for c in cards] == ["buy", "sell"]


def test_extract_from_flat_code_dict():
    aggregate = {"600519": {"symbol": "600519", "confidence": 0.6},
                 "000001": {"symbol": "000001", "confidence": 0.3}}
    cards = sig.extract_cards(aggregate)
    assert [c.symbol for c in cards] == ["600519", "000001"]


def test_extract_empty_and_junk_inputs():
    assert sig.extract_cards({}) == []
    assert sig.extract_cards("junk") == []
    assert sig.extract_cards(None) == []
    assert sig.extract_cards([]) == []


def test_extract_filters_records_without_symbol():
    aggregate = {"signals": [
        {"action": "buy", "confidence": 0.9},  # 缺 symbol,应被过滤
        {"symbol": "600519", "action": "buy"},
    ]}
    cards = sig.extract_cards(aggregate)
    assert [c.symbol for c in cards] == ["600519"]
    aggregate_flat = {"not-a-symbol": {"action": "buy"}, "600519": {"symbol": "600519"}}
    cards_flat = sig.extract_cards(aggregate_flat)
    assert [c.symbol for c in cards_flat] == ["600519"]


@pytest.fixture
def probe_artifact():
    """真实产物形状:src/strategy_signal_probe.py aggregate() 的输出结构。"""
    return {
        "as_of_date": "2026-08-14",
        "limit_basis": "prev_close",
        "probe_version": "1",
        "generated_at": "2026-08-14 20:00:00",
        "strategies_loaded": 2,
        "groups": {"trend": ["ma_crossover_v1", "rsi_reversion_v1"],
                   "volume": ["volume_breakout_v1"]},
        "symbols": {
            "600519": {
                "name": "贵州茅台",
                "st": False,
                "as_of_date": "2026-08-14",
                "groups": {
                    "trend": {
                        "level": "bullish",
                        "vote_ratio": 0.5,
                        "triggered": 1,
                        "total": 2,
                        "signals": {
                            "ma_crossover_v1": {"triggered": True, "entry_price": 1500.0,
                                                "entry_basis": "close", "reason": "MA5>MA20",
                                                "limit_up": False, "limit_down": False,
                                                "limit_basis": "prev_close",
                                                "insufficient_data": False},
                            "rsi_reversion_v1": {"triggered": False, "entry_price": None,
                                                 "entry_basis": None, "reason": "",
                                                 "limit_up": False, "limit_down": False,
                                                 "limit_basis": "prev_close",
                                                 "insufficient_data": False},
                        },
                    },
                    "volume": {
                        "level": "neutral",
                        "vote_ratio": 0.0,
                        "triggered": 0,
                        "total": 1,
                        "signals": {
                            "volume_breakout_v1": {"triggered": False, "entry_price": None,
                                                   "entry_basis": None, "reason": "",
                                                   "limit_up": False, "limit_down": False,
                                                   "limit_basis": "prev_close",
                                                   "insufficient_data": False},
                        },
                    },
                },
            },
            "000001": {
                "name": "平安银行",
                "st": False,
                "as_of_date": "2026-08-14",
                "groups": {},
            },
        },
        "data_issues": {},
        "strategy_errors": [],
    }


def test_extract_from_probe_artifact_symbols_shape(probe_artifact):
    cards = sig.extract_cards(probe_artifact)
    assert [c.symbol for c in cards] == ["600519", "600519", "600519"]
    assert [c.strategy for c in cards] == [
        "ma_crossover_v1", "rsi_reversion_v1", "volume_breakout_v1"]
    assert [c.as_of_date for c in cards] == ["2026-08-14"] * 3
    assert cards[0].entry_price == 1500.0
    assert cards[1].entry_price is None
    assert all(c.action is None for c in cards)
    assert all(c.stop_loss is None for c in cards)
    assert all(c.target_price is None for c in cards)
    assert all(c.confidence is None for c in cards)
    assert all(c.supports is None for c in cards)
    assert all(c.conflicts is None for c in cards)


def test_extract_from_probe_artifact_skips_empty_groups(probe_artifact):
    probe_artifact["symbols"]["000001"]["groups"] = {
        "trend": {"level": "neutral", "vote_ratio": 0.0, "triggered": 0, "total": 0,
                  "signals": {}},
    }
    cards = sig.extract_cards(probe_artifact)
    assert [c.symbol for c in cards] == ["600519", "600519", "600519"]


def test_extract_from_probe_artifact_date_falls_back_to_top(probe_artifact):
    entry = probe_artifact["symbols"]["000001"]
    entry.pop("as_of_date", None)
    entry["groups"] = {
        "trend": {"level": "neutral", "vote_ratio": 0.0, "triggered": 0, "total": 1,
                  "signals": {"volume_breakout_v1": {"triggered": False, "entry_price": None}}},
    }
    cards = sig.extract_cards(probe_artifact)
    assert len(cards) == 4
    assert cards[3].symbol == "000001"
    assert cards[3].strategy == "volume_breakout_v1"
    assert cards[3].as_of_date == "2026-08-14"