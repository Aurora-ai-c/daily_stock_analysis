# -*- coding: utf-8 -*-
"""E2E: real probe run against live data (network). Asserts the signal JSON
contract end-to-end: fetch -> evaluate -> aggregate -> renderable."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from src.alphaevo_bridge import render_strategy_signal_section
from src.strategy_signal_probe import run_probe

pytestmark = pytest.mark.network

SMOKE_SYMBOLS = ["600519", "300750"]  # 茅台 + 宁德，覆盖主板/创业板


def test_probe_contract_live():
    payload = asyncio.run(run_probe(symbols=SMOKE_SYMBOLS))
    # ── top-level contract ──
    assert payload["probe_version"] == "1"
    assert payload["limit_basis"] == "prev_close"
    assert payload["strategies_loaded"] == 5
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", payload["as_of_date"]), payload["as_of_date"]
    # ── per-symbol contract ──
    for code in SMOKE_SYMBOLS:
        assert code in payload["symbols"], f"{code} missing: {payload.get('data_issues')}"
        entry = payload["symbols"][code]
        assert entry["name"], "name must resolve"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["as_of_date"])
        for group_name, group in entry["groups"].items():
            assert group["level"] in {"bullish", "neutral", "bearish"}
            assert 0.0 <= group["vote_ratio"] <= 1.0
            assert group["total"] >= 1
            for sid, sig in group["signals"].items():
                assert sig["limit_basis"] == "prev_close"
                assert isinstance(sig["triggered"], bool)
                assert sig["reason"], f"{code}/{sid} missing reason"
    # ── degrade contract: strategy_errors may exist but JSON must stay valid ──
    assert isinstance(payload["strategy_errors"], list)
    assert isinstance(payload["data_issues"], dict)


def test_probe_renderable_live():
    """The probe output must render through the bridge without failure."""
    payload = asyncio.run(run_probe(symbols=SMOKE_SYMBOLS))

    async def fake(*a, **k):
        return payload

    with patch("src.strategy_signal_probe.run_probe", side_effect=fake), patch.dict(
        "os.environ", {"STRATEGY_SIGNALS_ENABLED": "true"}, clear=False
    ):
        text = render_strategy_signal_section([])
    assert text is not None
    assert "## 🧭 策略信号" in text
    for code in SMOKE_SYMBOLS:
        assert code in text