# -*- coding: utf-8 -*-
"""Offline-safe tests for the strategy-signal toggle bridge."""
import importlib

import src.alphaevo_bridge as bridge


def test_signals_enabled_default_true(monkeypatch):
    monkeypatch.delenv("STRATEGY_SIGNALS_ENABLED", raising=False)
    importlib.reload(bridge)
    assert bridge.signals_enabled() is True


def test_signals_enabled_false_disables(monkeypatch):
    monkeypatch.setenv("STRATEGY_SIGNALS_ENABLED", "false")
    importlib.reload(bridge)
    assert bridge.signals_enabled() is False
    importlib.reload(bridge)  # restore default for other tests


def test_render_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("STRATEGY_SIGNALS_ENABLED", "0")
    importlib.reload(bridge)
    from src.alphaevo_bridge import render_strategy_signal_section
    assert render_strategy_signal_section([{"symbols": {}}]) is None
    importlib.reload(bridge)
