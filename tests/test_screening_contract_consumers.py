# -*- coding: utf-8 -*-
"""screening / fundamental 消费点与契约层 FundamentalRaw 的兼容契约测试。"""
import pytest
from types import SimpleNamespace
from unittest.mock import patch
from data_provider.base import DataFetcherManager
from data_provider.contracts import FundamentalRaw
from datetime import date


def test_fundamental_raw_dump_keeps_keys_for_consumers():
    fr = FundamentalRaw(report_date=date(2026, 6, 30), fiscal_period="Q2", market="cn",
                        total_assets=1.0, revenue=2.0, net_income=0.5)
    d = fr.model_dump()
    assert d["report_date"] == date(2026, 6, 30)  # 消费者按原 key 读取
    assert d["total_assets"] == 1.0


# FundamentalRaw.model_dump() 会产出的顶层 key(不含 market——market 是旧路径既有 key)
_FUNDAMENTAL_CONTRACT_KEYS = (
    "report_date", "fiscal_period", "total_assets", "total_liabilities", "total_equity",
    "revenue", "net_income", "operating_cashflow", "investing_cashflow",
    "financing_cashflow", "gross_margin", "dividend_yield", "industry",
)


class _StubAkshareFetcher:
    """契约合并的目标源替身:仅提供 to_fundamental,真实走 _merge_fundamental_contract 路径。"""

    name = "AkshareFetcher"
    priority = 1

    @staticmethod
    def to_fundamental(payload: dict):
        return FundamentalRaw(
            report_date=date.fromisoformat(str(payload.get("report_date"))),
            fiscal_period="Q2",
            market=str(payload.get("market") or "cn"),
            total_assets=1.0,
            revenue=payload.get("revenue"),
            net_income=payload.get("net_income"),
            operating_cashflow=payload.get("operating_cashflow"),
            gross_margin=payload.get("gross_margin"),
        )


def _build_manager():
    return DataFetcherManager(fetchers=[_StubAkshareFetcher()])


def _run_fundamental_context(manager, connector_v2_enabled):
    cfg = SimpleNamespace(
        enable_fundamental_pipeline=True,
        fundamental_cache_ttl_seconds=0,
        fundamental_stage_timeout_seconds=1.5,
        fundamental_fetch_timeout_seconds=0.8,
        fundamental_retry_max=1,
        connector_v2_enabled=connector_v2_enabled,
    )
    quote = SimpleNamespace(
        pe_ratio=12.3,
        pb_ratio=2.1,
        total_mv=1.0e11,
        circ_mv=7.0e10,
        source=SimpleNamespace(value="tencent"),
    )
    bundle = {
        "status": "partial",
        "growth": {"gross_margin": 0.42},
        "earnings": {
            "financial_report": {
                "report_date": "2026-06-30",
                "revenue": 2.0,
                "net_profit_parent": 0.5,
                "operating_cash_flow": 1.0,
            }
        },
        "institution": {},
        "source_chain": ["growth:akshare"],
        "errors": [],
    }
    with patch("src.config.get_config", return_value=cfg), \
            patch.object(manager, "get_realtime_quote", return_value=quote), \
            patch("data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle",
                  return_value=bundle), \
            patch.object(manager, "get_capital_flow_context",
                         return_value={"status": "not_supported", "source_chain": []}), \
            patch.object(manager, "get_dragon_tiger_context",
                         return_value={"status": "not_supported", "source_chain": []}), \
            patch.object(manager, "get_board_context",
                         return_value={"status": "not_supported", "source_chain": []}):
        return manager.get_fundamental_context("600519", budget_seconds=1.5)


def test_fundamental_contract_merge_flag_off_zero_side_effect():
    """flag OFF:目标源存在时顶层 key 全集与旧路径一致,契约合并 key 零泄漏。"""
    ctx = _run_fundamental_context(_build_manager(), connector_v2_enabled=False)
    assert set(ctx.keys()) == {
        "market", "status", "coverage", "source_chain", "errors", "elapsed_ms",
        "valuation", "growth", "earnings", "institution",
        "capital_flow", "dragon_tiger", "boards",
    }
    assert not any(key in ctx for key in _FUNDAMENTAL_CONTRACT_KEYS)


def test_fundamental_contract_merge_flag_on_merges_contract_keys():
    """flag ON:目标源 to_fundamental 产出 FundamentalRaw 后 model_dump 合并进返回 dict。"""
    ctx = _run_fundamental_context(_build_manager(), connector_v2_enabled=True)
    assert ctx["report_date"] == date(2026, 6, 30)
    assert ctx["fiscal_period"] == "Q2"
    assert ctx["market"] == "cn"
    assert ctx["total_assets"] == 1.0
    assert ctx["revenue"] == 2.0
    assert ctx["net_income"] == 0.5  # 来自旧 bundle key net_profit_parent
    assert ctx["operating_cashflow"] == 1.0  # 来自旧 bundle key operating_cash_flow
    assert ctx["gross_margin"] == 0.42  # 来自 growth 块
    for block in ("valuation", "growth", "earnings", "institution",
                  "capital_flow", "dragon_tiger", "boards", "coverage"):
        assert block in ctx
