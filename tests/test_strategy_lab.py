# -*- coding: utf-8 -*-
"""Strategy lab: offline unit tests for watchlist sampling, the builtin
overwrite guard and the no-look-ahead market regime behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from alphaevo.models.enums import MarketType
from alphaevo.strategy.store import StrategyStore

STRATEGY_DIR = Path(__file__).resolve().parent.parent / "strategy_signals"
MA_CROSSOVER_YAML = STRATEGY_DIR / "ma_crossover_v1.yaml"


def _flip_market(yaml_text: str, market: str) -> str:
    doc = yaml.safe_load(yaml_text)
    doc["meta"]["market"] = market
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


# ── watchlist sampling (DataFetcherManager.get_stock_list) ────────────


class TestWatchlistStockList:
    def test_returns_watchlist_from_env(self, monkeypatch):
        from data_provider.base import DataFetcherManager

        manager = DataFetcherManager(fetchers=[])
        monkeypatch.setenv("STOCK_LIST", "600519, 300750\n002594")
        monkeypatch.setattr(
            manager,
            "get_stock_name",
            lambda stock_code, allow_realtime=True: f"股票{stock_code}",
        )

        rows = manager.get_stock_list()

        assert [r["code"] for r in rows] == ["600519", "300750", "002594"]
        assert rows[0]["name"] == "股票600519"
        assert rows[0]["market"] == "A_SHARE"

    def test_empty_when_no_env(self, monkeypatch):
        from data_provider.base import DataFetcherManager

        manager = DataFetcherManager(fetchers=[])
        monkeypatch.delenv("STOCK_LIST", raising=False)
        monkeypatch.setattr(manager, "get_stock_name", lambda stock_code, allow_realtime=True: None)

        assert manager.get_stock_list() == []


# ── builtin strategies must not overwrite user strategies ─────────────


class TestBuiltinOverwriteGuard:
    def test_builtin_skips_existing_user_strategy(self, tmp_path):
        if not MA_CROSSOVER_YAML.is_file():
            pytest.skip("strategy_signals/ma_crossover_v1.yaml not present")

        user_yaml = tmp_path / "user.yaml"
        user_yaml.write_text(MA_CROSSOVER_YAML.read_text(encoding="utf-8"), encoding="utf-8")

        builtin_dir = tmp_path / "builtin"
        builtin_dir.mkdir()
        (builtin_dir / "ma_crossover_v1.yaml").write_text(
            _flip_market(MA_CROSSOVER_YAML.read_text(encoding="utf-8"), "us"),
            encoding="utf-8",
        )

        store = StrategyStore(db_path=str(tmp_path / "store.db"))
        store.import_from_file(user_yaml)
        store.import_builtin_strategies(builtin_dir)

        stored = store.get("ma_crossover_v1")
        assert stored is not None
        assert stored.meta.market == MarketType.A_SHARE

    def test_builtin_still_imported_when_id_absent(self, tmp_path):
        if not MA_CROSSOVER_YAML.is_file():
            pytest.skip("strategy_signals/ma_crossover_v1.yaml not present")

        builtin_dir = tmp_path / "builtin"
        builtin_dir.mkdir()
        doc = yaml.safe_load(MA_CROSSOVER_YAML.read_text(encoding="utf-8"))
        doc["meta"]["id"] = "other_v2"
        doc["meta"]["market"] = "us"
        (builtin_dir / "other_v2.yaml").write_text(
            yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        store = StrategyStore(db_path=str(tmp_path / "store.db"))
        assert store.import_builtin_strategies(builtin_dir) == 1
        assert store.get("other_v2") is not None


# ── DSA market context must not gate historical backtests ─────────────


class TestNoLookAheadRegime:
    @staticmethod
    def _adapter() -> object:
        from alphaevo.data.adapters.dsa import DSAAdapter

        class _FakeManager:
            pass

        adapter = DSAAdapter.__new__(DSAAdapter)
        adapter._manager = _FakeManager()
        adapter._sector_rankings_cache = None
        return adapter

    def test_market_context_regime_stays_none(self, monkeypatch):
        adapter = self._adapter()
        monkeypatch.setattr(
            adapter,
            "_fetch_market_payload",
            lambda: (
                {
                    "up_count": 3200,
                    "down_count": 1200,
                    "flat_count": 300,
                    "limit_up_count": 80,
                    "limit_down_count": 20,
                },
                [{"name": "上证指数", "change_pct": 0.5}],
                ([{"name": "银行"}], [{"name": "地产"}]),
            ),
        )

        ctx = asyncio.run(adapter.get_market_context(MarketType.A_SHARE))

        assert ctx is not None
        assert ctx.regime is None  # today's snapshot must never gate history
        assert ctx.breadth is not None
        assert ctx.breadth > 0.5

    def test_market_context_none_when_payload_empty(self, monkeypatch):
        adapter = self._adapter()
        monkeypatch.setattr(adapter, "_fetch_market_payload", lambda: ({}, [], []))

        ctx = asyncio.run(adapter.get_market_context(MarketType.A_SHARE))

        assert ctx is None
