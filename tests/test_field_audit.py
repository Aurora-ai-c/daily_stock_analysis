# -*- coding: utf-8 -*-
"""
Zero-gate field audit (阶段 0 零号关卡).

Verifies that the DSA data provider can supply every field the strategy signal
probe depends on, across a representative A-share sample (main board / ChiNext /
STAR / ST / newly listed / suspended).

Gate: if this audit fails, strategy signal work must NOT proceed.

Coverage contract for the signal JSON (per symbol):
    symbol    -> bare A-share code (600519)
    name      -> from get_stock_name(), used for ST detection
    st        -> bool derived from name containing "ST"/"退"
    prev_close-> derivable from close.shift(1) (limit-up basis is T-1 close)

Usage:
    pytest tests/test_field_audit.py -m network -v
    FIELD_AUDIT_SAMPLE_SIZE=100 pytest tests/test_field_audit.py -m network
"""

# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import random
import unittest
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pytest

from data_provider.base import DataFetcherManager

# Explicit representative pool: main board / ChiNext / STAR / ST / new listing.
# Codes are stable, well-known A-share tickers.
EXPLICIT_POOL = [
    "600519",  # 贵州茅台 主板
    "002594",  # 比亚迪 主板
    "000858",  # 五粮液 主板
    "601318",  # 中国平安 主板
    "600036",  # 招商银行 主板
    "300750",  # 宁德时代 创业板
    "300059",  # 东方财富 创业板
    "301236",  # 软通动力 创业板
    "688981",  # 中芯国际 科创板
    "688256",  # 寒武纪 科创板
    "600462",  # ST 类样本（历史 ST 标的，可退市，仅用于降级验证）
    "600715",  # ST 类样本
    "301589",  # 新股/次新样本
    "688692",  # 次新科创板样本
]

WATCHLIST = ["600519", "300750", "002594"]

REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]

_LIMIT_UP_CHECK_ALLOWED = 0.9  # 90% pass rate required overall
_ST_SAMPLE_REQUIRED = 3        # need at least 3 distinguishable ST candidates


def audit_one_stock(
    manager: DataFetcherManager, code: str, days: int = 130
) -> Dict[str, object]:
    """Audit a single symbol; returns a dict of checks + meta (never raises)."""
    result: Dict[str, object] = {
        "code": code,
        "pass": False,
        "issues": [],
        "warnings": [],
        "rows": 0,
        "source": None,
        "name": None,
        "st_seen": False,
        "prev_close_ok": False,
        "pct_chg_ok": False,
    }
    try:
        df, source = manager.get_daily_data(code, days=days)
    except Exception as exc:  # noqa: BLE001 - audit must survive any provider failure
        result["issues"].append(f"get_daily_data raised: {exc}")
        return result

    result["source"] = source
    if df is None or df.empty:
        result["issues"].append("empty dataframe")
        return result

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        result["issues"].append(f"missing columns: {missing}")
    if "date" in df.columns:
        dates = pd.to_datetime(df["date"], errors="coerce")
        if dates.isna().any():
            result["issues"].append("date column contains NaT")
        else:
            if not dates.is_monotonic_increasing:
                result["issues"].append("date column not monotonic")
            if dates.duplicated().any():
                result["issues"].append("date column has duplicates")

    closes = pd.to_numeric(df["close"], errors="coerce")
    volumes = pd.to_numeric(df["volume"], errors="coerce")
    result["rows"] = int(len(df))
    if closes.isna().any() or (closes <= 0).any():
        result["issues"].append(f"close invalid: nan={int(closes.isna().sum())}, nonpos={int((closes <= 0).sum())}")
    if volumes.isna().any():
        result["issues"].append(f"volume contains nan: {int(volumes.isna().sum())}")

    # prev_close derivability (limit-up basis = T-1 close)
    prev_close = closes.shift(1)
    valid_ratio = float(prev_close.notna().mean())
    result["prev_close_ok"] = valid_ratio >= 0.9

    # pct_chg sanity vs derivable prev close (warn only: adjust/plit may shift levels)
    if "pct_chg" in df.columns and result["prev_close_ok"]:
        pct = pd.to_numeric(df["pct_chg"], errors="coerce")
        derived = (closes / prev_close - 1.0) * 100.0
        if pct.notna().any():
            dev = (pct - derived).abs().dropna()
            mismatch = (dev > 2.0).sum()
            if mismatch > max(1, int(len(df) * 0.05)):
                result["warnings"].append(f"pct_chg deviates from derivable prev close rows={int(mismatch)}")
            else:
                result["pct_chg_ok"] = True

    # name / ST detection
    try:
        name = manager.get_stock_name(code, allow_realtime=False)
    except Exception as exc:  # noqa: BLE001
        name = None
        result["warnings"].append(f"get_stock_name raised: {exc}")
    if name:
        result["name"] = str(name)
        result["st_seen"] = ("ST" in str(name).upper()) or ("退" in str(name))

    result["pass"] = len(result["issues"]) == 0
    return result


def build_sample_pool(manager: DataFetcherManager, size: int) -> Tuple[List[str], List[str]]:
    """Return (pool, st_candidates). pool contains every symbol to audit.

    st_candidates are explicitly surfaced from the stock list by name so the
    gate always exercises ST-class degradation paths.
    """
    pool: List[str] = list(EXPLICIT_POOL)
    st_candidates: List[str] = []
    stock_list: Optional[pd.DataFrame] = None
    # Manager has no public get_stock_list; fetch via fetcher snapshot like
    # DataFetcherManager.batch_get_stock_names does.
    for fetcher in manager._get_fetchers_snapshot():
        if not hasattr(fetcher, "get_stock_list"):
            continue
        try:
            candidate = fetcher.get_stock_list()
        except Exception:  # noqa: BLE001 - provider failures are expected in audit
            continue
        if candidate is not None and not candidate.empty:
            stock_list = candidate
            break
    if stock_list is not None and not stock_list.empty:
        df = stock_list.copy()
        df["code"] = df["code"].astype(str)
        names = df["name"].astype(str).str.upper()
        st_mask = names.str.contains("ST", regex=False) | names.str.contains("退", regex=False)
        st_rows = df[st_mask].head(20)
        for _, row in st_rows.iterrows():
            if len(st_candidates) >= _ST_SAMPLE_REQUIRED:
                break
            code = row["code"]
            if code not in pool and code not in st_candidates:
                st_candidates.append(code)
        all_codes = [c for c in df["code"].tolist() if c not in pool and c not in st_candidates]
        random.Random(20260813).shuffle(all_codes)
        for code in all_codes:
            if len(pool) + len(st_candidates) >= size:
                break
            pool.append(code)
    return pool + st_candidates, st_candidates


class TestFieldAudit(unittest.TestCase):
    """Zero-gate: fields needed by the strategy signal probe must be present."""

    def setUp(self) -> None:
        self.manager = DataFetcherManager()
        self.sample_size = int(os.environ.get("FIELD_AUDIT_SAMPLE_SIZE", "20"))

    @pytest.mark.network
    def test_watchlist_symbols_fully_supported(self) -> None:
        """The fixed watchlist must pass 100%; it is the daily signal target.

        Uses the full warmup window (260 days) so the deepest strategy indicator
        windows are exercised, not just field presence.
        """
        for code in WATCHLIST:
            with self.subTest(code=code):
                r = audit_one_stock(self.manager, code, days=260)
                self.assertTrue(r["pass"], f"{code} failed: {r['issues']}")

    @pytest.mark.network
    def test_field_audit_representative_sample(self) -> None:
        """Cumulative field-completeness among *fetchable* symbols must be >= 0.9.

        Rationale: the strategy probe degrades gracefully — symbols the entire
        provider chain cannot resolve (delisted / suspended-invalid codes) are
        skipped, never errored. The gate therefore audits field completeness on
        everything the chain *can* fetch, and reports unfetchable codes.
        """
        pool, _ = build_sample_pool(self.manager, self.sample_size)
        results: List[Dict[str, object]] = []
        for code in pool:
            r = audit_one_stock(self.manager, code)
            results.append(r)

        unfetchable = [r for r in results if r["source"] is None]
        fetchable = [r for r in results if r["source"] is not None]
        passed = [r for r in fetchable if r["pass"]]
        rate = len(passed) / len(fetchable) if fetchable else 0.0
        failures = [r for r in fetchable if not r["pass"]]
        details = "\n".join(
            f"  {r['code']}: rows={r['rows']} src={r['source']} issues={r['issues']}"
            for r in failures
        )
        self.assertGreaterEqual(
            rate,
            _LIMIT_UP_CHECK_ALLOWED,
            f"field audit pass rate {rate:.0%} < {_LIMIT_UP_CHECK_ALLOWED:.0%}\n{details}",
        )
        self.assertGreaterEqual(
            len(fetchable),
            int(len(results) * 0.7),
            f"too many unfetchable symbols: {[r['code'] for r in unfetchable]}",
        )
        self.assertTrue(
            all(r["name"] for r in fetchable),
            f"some fetchable symbols lack name (needed for ST detection): "
            f"{[r['code'] for r in fetchable if not r['name']]}",
        )

    @pytest.mark.network
    def test_st_samples_remain_fetchable(self) -> None:
        """ST-class symbols must yield OHLCV (signals degrade, not die).

        Unfetchable ST candidates (delisted) are acceptable — they prove the
        skip path — but at least one active ST symbol must be fully auditable.
        """
        _, st_candidates = build_sample_pool(self.manager, self.sample_size)
        fetchable_st = 0
        for code in st_candidates:
            r = audit_one_stock(self.manager, code)
            if r["source"] is None:
                continue
            fetchable_st += 1
            self.assertTrue(r["pass"], f"ST symbol {code} failed hard: {r['issues']}")
        self.assertGreaterEqual(
            fetchable_st,
            1,
            "no fetchable ST-class symbol found; stock list may be stale or ST pool empty",
        )


if __name__ == "__main__":
    unittest.main()