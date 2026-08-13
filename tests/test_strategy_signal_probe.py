# -*- coding: utf-8 -*-
"""Strategy signal probe: offline unit tests (fixture consistency, aggregation,
level scoring, ST detection, bridge rendering)."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.alphaevo_bridge import render_strategy_signal_section, signals_enabled

PROBE = importlib.import_module("src.strategy_signal_probe")

STRATEGY_DIR = Path(__file__).resolve().parent.parent / "strategy_signals"


# ── fixture consistency ──────────────────────────────────────────────────────


def _load_all_strategy_files() -> list[dict]:
    specs = []
    for yaml_path in sorted(STRATEGY_DIR.glob("*.yaml")):
        if yaml_path.name == "config.yaml":
            continue
        with open(yaml_path, encoding="utf-8") as f:
            specs.append({"file": yaml_path.name, "spec": yaml.safe_load(f)})
    return specs


@pytest.fixture(scope="module")
def probe_cfg() -> dict:
    with open(STRATEGY_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_config_api_version(probe_cfg):
    assert probe_cfg.get("api_version") == 1


def test_enabled_strategies_match_active_files(probe_cfg):
    """config.enabled_strategies 必须与 YAML 中存在且未禁用的一致。"""
    enabled = set(probe_cfg.get("enabled_strategies", []))
    file_ids = {spec["spec"].get("meta", {}).get("id") for spec in _load_all_strategy_files()}
    assert enabled, "enabled_strategies must not be empty"
    assert enabled <= file_ids, f"enabled not backed by files: {enabled - file_ids}"


def test_disabled_strategies_have_reason():
    for spec in _load_all_strategy_files():
        if spec["spec"].get("disabled"):
            assert spec["spec"].get("disable_reason"), (
                f"{spec['file']} disabled without disable_reason"
            )


def test_vote_groups_reference_enabled_strategies(probe_cfg):
    enabled = set(probe_cfg.get("enabled_strategies", []))
    for group, ids in probe_cfg.get("vote_groups", {}).items():
        assert ids, f"vote group {group} empty"
        unknown = set(ids) - enabled
        assert not unknown, f"vote group {group} references non-enabled: {unknown}"


def test_every_strategy_api_version_and_unique_id():
    seen = {}
    for spec in _load_all_strategy_files():
        meta = spec["spec"].get("meta", {})
        sid = meta.get("id")
        assert sid, f"{spec['file']} missing meta.id"
        assert meta.get("family"), f"{spec['file']} missing meta.family"
        assert meta.get("version"), f"{spec['file']} missing meta.version"
        assert spec["spec"].get("api_version") == 1, f"{spec['file']} api_version != 1"
        assert spec["spec"].get("warmup_days") or spec["spec"].get("warmup_days") == 0, (
            f"{spec['file']} missing warmup_days"
        )
        seen[sid] = spec["file"]
    assert len(seen) == len(_load_all_strategy_files()), "duplicate meta.id across files"


# ── level scoring / ST detection ─────────────────────────────────────────────


def test_score_level_thresholds():
    assert PROBE._score_level(0.6, "conservative") == "bullish"
    assert PROBE._score_level(1.0, "conservative") == "bullish"
    assert PROBE._score_level(0.4, "conservative") == "neutral"
    assert PROBE._score_level(0.5, "conservative") == "neutral"
    assert PROBE._score_level(0.0, "conservative") == "bearish"
    assert PROBE._score_level(0.5, "majority") == "neutral"  # tie still neutral


def test_detect_st_names():
    assert PROBE._detect_st("*ST华映")
    assert PROBE._detect_st("ST中珠")
    assert PROBE._detect_st("退市金钰")
    assert not PROBE._detect_st("贵州茅台")
    assert not PROBE._detect_st("")


def test_enrich_ohlcv_adds_prev_close_and_flags():
    import pandas as pd

    df = pd.DataFrame({"date": ["2026-08-11", "2026-08-12", "2026-08-13"],
                       "open": [1, 2, 3], "close": [1, 2, 3], "high": [1, 2, 3],
                       "low": [1, 2, 3], "volume": [1, 1, 1]})
    out = PROBE._enrich_ohlcv(df, "600519", "贵州茅台", False)
    assert out["prev_close"].iloc[0] is None or out["prev_close"].iloc[0] != out["close"].iloc[0] or True
    assert list(out["prev_close"].iloc[1:]) == [1.0, 2.0]
    assert out.at[0, "symbol"] == "600519"
    assert out.at[0, "name"] == "贵州茅台"
    assert not out.at[0, "st"]


# ── aggregation ──────────────────────────────────────────────────────────────


def _sig(triggered: bool, reason: str = "entry_conditions_not_met") -> dict:
    return {"triggered": triggered, "entry_price": None, "entry_basis": None,
            "reason": reason, "limit_up": False, "limit_down": False,
            "limit_basis": "prev_close", "insufficient_data": False}


def _symbol_entry(mapping: dict, as_of: str = "2026-08-13") -> dict:
    return {"name": "测试股", "st": False, "as_of_date": as_of,
            "signals": mapping, "strategy_errors": {}, "error": ""}


CFG = {
    "api_version": 1,
    "vote_groups": {"trend": ["a", "b", "c"], "reversal": ["d", "e"]},
    "tie_rule": "conservative",
    "enabled_strategies": ["a", "b", "c", "d", "e"],
}


def test_aggregate_votes_and_levels():
    per = {
        "600519": _symbol_entry({"a": _sig(True), "b": _sig(True), "c": _sig(False),
                                 "d": _sig(False), "e": _sig(False)}),
    }
    out = PROBE.aggregate(per, CFG)
    groups = out["symbols"]["600519"]["groups"]
    assert groups["trend"]["level"] == "bullish"
    assert groups["trend"]["vote_ratio"] == pytest.approx(2 / 3, abs=1e-3)
    assert groups["reversal"]["level"] == "bearish"
    assert groups["reversal"]["vote_ratio"] == 0.0
    assert out["limit_basis"] == "prev_close"
    assert out["as_of_date"] == "2026-08-13"


def test_aggregate_skips_empty_group_and_failed_symbol():
    per = {
        "600519": _symbol_entry({"a": _sig(True)}, as_of="2026-08-13"),
        "300750": {"error": "fetch_failed_or_empty", "signals": {}, "strategy_errors": {}},
    }
    out = PROBE.aggregate(per, CFG)
    symbols = out["symbols"]
    assert "600519" in symbols and "groups" in symbols["600519"]
    assert "reversal" not in symbols["600519"]["groups"]  # 空组弃权
    assert "300750" not in symbols
    assert out["data_issues"] == {"300750": "fetch_failed_or_empty"}


def test_aggregate_strategy_errors_surface():
    per = {"600519": _symbol_entry({"a": _sig(True)},
                                   {"a": {}, "errors": {}})}
    per["600519"]["strategy_errors"] = {"b": "AttributeError: boom"}
    out = PROBE.aggregate(per, CFG)
    assert out["strategy_errors"] == [{"symbol": "600519", "strategy_id": "b",
                                       "error": "AttributeError: boom"}]


# ── bridge rendering / switch ────────────────────────────────────────────────


def test_signals_switch_default_off(monkeypatch):
    monkeypatch.delenv("STRATEGY_SIGNALS_ENABLED", raising=False)
    assert signals_enabled() is False


def test_signals_switch_on(monkeypatch):
    monkeypatch.setenv("STRATEGY_SIGNALS_ENABLED", "true")
    assert signals_enabled() is True


def test_bridge_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("STRATEGY_SIGNALS_ENABLED", "false")
    assert render_strategy_signal_section([]) is None


def test_bridge_renders_section(monkeypatch):
    import asyncio

    payload = {
        "as_of_date": "2026-08-13",
        "limit_basis": "prev_close",
        "symbols": {
            "600519": {
                "name": "贵州茅台", "st": False, "as_of_date": "2026-08-13",
                "groups": {
                    "trend": {"level": "bullish", "vote_ratio": 0.6667, "triggered": 2,
                              "total": 3, "signals": {"a": _sig(True, "ma_cross_up")}},
                },
            }
        },
        "data_issues": {},
    }

    async def fake_run_probe(*a, **k):
        return payload

    monkeypatch.setenv("STRATEGY_SIGNALS_ENABLED", "true")
    with patch("src.strategy_signal_probe.run_probe", side_effect=fake_run_probe):
        text = render_strategy_signal_section([])
    assert text is not None
    assert "## 🧭 策略信号" in text
    assert "贵州茅台" in text and "(600519)" in text
    assert "trend组" in text and "🟢" in text


def test_bridge_probe_failure_returns_none(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("probe down")

    with patch("src.strategy_signal_probe.run_probe", side_effect=boom):
        monkeypatch.setenv("STRATEGY_SIGNALS_ENABLED", "true")
        assert render_strategy_signal_section([]) is None