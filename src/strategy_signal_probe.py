# -*- coding: utf-8 -*-
"""
策略信号探针（deterministic daily strategy signal probe）

三层结构：
1. fetch  — 从 DataFetcherManager 拉每只股票的日线（按各策略最大 warmup），
            补 prev_close/symbol/name/st 列（涨停判定与 ST 排除依赖这些）
2. run    — 每 策略×股票 调用 alphaevo BacktestEngine.signal_at_last_bar()
            评估“今天是否触发入场”（不进回测循环，确定性、单点）
3. aggregate — 按 config.yaml 的 vote_groups 分组投票，输出信号 JSON

信号 JSON 契约（评审已定）：
- 必含 as_of_date、limit_basis: "prev_close"（涨停判定基于 T-1 收盘价）
- 每股每个 vote_group 一个聚合信号（级别 🟢/🟡/🔴 + 组内各策略明细）
- 数据不足/取数失败 → data_issues 记录，主流程不中断
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

PROBE_VERSION = "1"
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent / "strategy_signals"
DEFAULT_TIMEOUT_SECONDS = 1200
DEFAULT_MAX_CONCURRENCY = 4
_WARMUP_BUFFER_BARS = 60  # 停牌/节假日缓冲，保证指标窗口完整

# 信号级别阈值（按组内触发比例）
_BULL_RATIO = 0.6  # vote_ratio >= 0.6 → 🟢
_TIE_RATIO = 0.5  # 恰好平票（conservative）→ 🟡


class ProbeError(Exception):
    """Hard probe failure (config, watchlist). Individual symbol/strategy
    failures never raise — they degrade into data_issues / strategy_errors."""


def load_probe_config(config_dir: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Load strategy_signals/config.yaml + enabled strategy specs.

    Returns (config, strategies) where each strategy is
    {"strategy_id": str, "path": Path, "content": str (raw YAML text)}.
    """
    cfg_path = config_dir / "config.yaml"
    if not cfg_path.exists():
        raise ProbeError(f"strategy signal config not found: {cfg_path}")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ProbeError(f"invalid strategy signal config (expected dict): {cfg_path}")

    enabled = set(cfg.get("enabled_strategies", []))
    strategies: List[Dict[str, Any]] = []
    for yaml_path in sorted(config_dir.glob("*.yaml")):
        if yaml_path.name == "config.yaml":
            continue
        try:
            with open(yaml_path, encoding="utf-8") as f:
                content = f.read()
            spec = yaml.safe_load(content)
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("[probe] skip unreadable strategy %s: %s", yaml_path.name, exc)
            continue
        if not isinstance(spec, dict):
            continue
        strategy_id = (spec.get("meta") or {}).get("id")
        if not strategy_id:
            logger.warning("[probe] strategy %s has no meta.id; skipped", yaml_path.name)
            continue
        if spec.get("disabled"):
            logger.info(
                "[probe] strategy %s disabled: %s",
                strategy_id,
                spec.get("disable_reason", ""),
            )
            continue
        if strategy_id not in enabled:
            continue
        strategies.append({"strategy_id": strategy_id, "path": yaml_path, "content": content})
    if not strategies:
        raise ProbeError("no enabled strategies found in strategy_signals/ (check config.yaml)")
    return cfg, strategies


def resolve_watchlist(cfg: Dict[str, Any]) -> List[str]:
    """Resolve symbols to probe: config override > STOCK_LIST env (incl. .env).

    Deliberately avoids the AppSettings singleton (import cycles for a CLI
    tool); loads local `.env` via dotenv when present, mirroring setup_env.
    """
    override = cfg.get("watchlist_override") or []
    if override:
        return [str(c) for c in override]
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001 - dotenv optional in prod runner
        pass
    from src.services.stock_list_parser import split_stock_list

    codes = [str(c) for c in split_stock_list(os.getenv("STOCK_LIST", "")) if c]
    if not codes:
        raise ProbeError(
            "watchlist empty: set STOCK_LIST in .env or watchlist_override in config.yaml"
        )
    return codes


def _warmup_days_for(cfg: Dict[str, Any], spec: Dict[str, Any]) -> int:
    """Per-strategy required data window (with buffer for suspension gaps)."""
    try:
        import yaml as _yaml

        parsed = _yaml.safe_load(spec["content"]) or {}
    except _yaml.YAMLError:
        parsed = {}
    warmup = parsed.get("warmup_days") or 30
    return int(warmup) + _WARMUP_BUFFER_BARS


def _enrich_ohlcv(df: pd.DataFrame, code: str, name: str, is_st: bool) -> pd.DataFrame:
    """Prepare the frame for alphaevo signal evaluation.

    Adds row-level prev_close (T-1 basis for limit-up checks), plus symbol /
    name / st columns consumed by the market rule checker's board thresholds.
    """
    out = df.reset_index(drop=True).copy()
    out["symbol"] = code
    out["name"] = name
    out["st"] = bool(is_st)
    if "prev_close" not in out.columns:
        out["prev_close"] = out["close"].shift(1)
    return out


def _detect_st(name: str) -> bool:
    upper = (name or "").upper()
    return "ST" in upper or "退" in upper


def _score_level(vote_ratio: float, tie_rule: str) -> str:
    """Map group vote ratio to a 3-level signal label (🟢/🟡/🔴)."""
    if vote_ratio >= _BULL_RATIO:
        return "bullish"
    if vote_ratio > 0:
        if vote_ratio == _TIE_RATIO and tie_rule == "conservative":
            return "neutral"
        return "neutral"
    return "bearish"


def _build_contexts(names: Dict[str, str], st_flags: Dict[str, bool]) -> Dict[str, Any]:
    """Build per-symbol IndicatorContext for alphaevo indicators (ST injection)."""
    from alphaevo.models.enums import MarketType
    from alphaevo.models.market import IndicatorContext, StockInfo

    return {
        code: IndicatorContext(
            stock_info=StockInfo(
                symbol=code,
                name=name,
                market=MarketType.A_SHARE,
                is_st=bool(st_flags.get(code, False)),
            )
        )
        for code, name in names.items()
    }


def _signal_to_dict(signal: Any) -> Dict[str, Any]:
    """Normalize a LastBarSignal into a JSON-safe record."""
    return {
        "triggered": bool(signal.triggered),
        "entry_price": float(signal.entry_price) if signal.entry_price is not None else None,
        "entry_basis": signal.entry_basis,
        "reason": signal.reason,
        "limit_up": bool(signal.limit_up),
        "limit_down": bool(signal.limit_down),
        "limit_basis": signal.limit_basis,
        "insufficient_data": bool(signal.insufficient_data),
    }


def aggregate(per_symbol: Dict[str, Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Group signals by vote_groups and attach a single level per group.

    - 空组（无任何策略信号）→ 不输出该组（弃权）
    - 组内每策略一票；vote_ratio = triggered / group_size
    - tie_rule=conservative → 平票取更低级别
    """
    tie_rule = cfg.get("tie_rule", "conservative")
    vote_groups: Dict[str, List[str]] = cfg.get("vote_groups", {})

    aggregated: Dict[str, Dict[str, Any]] = {}
    data_issues: Dict[str, str] = {}
    strategy_errors: List[Dict[str, str]] = []

    for code, entry in per_symbol.items():
        if entry.get("error"):
            data_issues[code] = entry["error"]
            continue
        groups_out: Dict[str, Dict[str, Any]] = {}
        for group_name, ids in vote_groups.items():
            present = {sid: entry["signals"].get(sid) for sid in ids}
            present = {sid: s for sid, s in present.items() if s is not None}
            if not present:
                continue  # 空组弃权
            triggered = sum(1 for s in present.values() if s["triggered"])
            ratio = triggered / len(present)
            groups_out[group_name] = {
                "level": _score_level(ratio, tie_rule),
                "vote_ratio": round(ratio, 4),
                "triggered": triggered,
                "total": len(present),
                "signals": present,
            }
        if groups_out:
            aggregated[code] = {
                "name": entry.get("name") or code,
                "st": bool(entry.get("st")),
                "as_of_date": entry.get("as_of_date") or "",
                "groups": groups_out,
            }
        for sid, err in entry.get("strategy_errors", {}).items():
            strategy_errors.append({"symbol": code, "strategy_id": sid, "error": err})

    return {
        "as_of_date": _latest_date(per_symbol),
        "limit_basis": "prev_close",
        "probe_version": PROBE_VERSION,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "strategies_loaded": len(cfg.get("enabled_strategies", [])),
        "groups": {g: ids for g, ids in vote_groups.items()},
        "symbols": aggregated,
        "data_issues": data_issues,
        "strategy_errors": strategy_errors,
    }


def _latest_date(per_symbol: Dict[str, Dict[str, Any]]) -> str:
    """Return the newest report date across successfully fetched symbols."""
    dates = [str(e["as_of_date"]) for e in per_symbol.values() if e.get("as_of_date")]
    return max(dates) if dates else date.today().isoformat()


async def run_probe(
    config_dir: Path = DEFAULT_CONFIG_DIR,
    data_manager: Any = None,
    symbols: Optional[List[str]] = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> Dict[str, Any]:
    """Run the full probe: fetch → run → aggregate. Returns signal JSON dict.

    Never raises for individual symbol/strategy failures — degrading is the
    contract; hard failures (config unreadable, no enabled strategies, empty
    watchlist, aggregate timeout) propagate as ProbeError.
    """
    from data_provider.base import DataFetcherManager

    cfg, strategies = load_probe_config(config_dir)
    watchlist = symbols or resolve_watchlist(cfg)
    manager = data_manager or DataFetcherManager()

    # ── fetch layer ──────────────────────────────────────────────────────
    names: Dict[str, str] = {}
    st_flags: Dict[str, bool] = {}
    frames: Dict[str, pd.DataFrame] = {}
    fetch_issues: Dict[str, str] = {}
    max_days = max(_warmup_days_for(cfg, s) for s in strategies)

    for code in watchlist:
        try:
            raw_name = manager.get_stock_name(code, allow_realtime=False)
            name = str(raw_name) if raw_name else code
        except Exception as exc:  # noqa: BLE001 - name lookup must not kill probe
            name = code
            logger.warning("[probe] name lookup failed for %s: %s", code, exc)
        names[code] = name
        st_flags[code] = _detect_st(name)

    sem = asyncio.Semaphore(max(1, max_concurrency))

    async def _fetch_one_guarded(code: str) -> Tuple[str, Optional[pd.DataFrame]]:
        async with sem:
            try:
                raw, _ = await asyncio.to_thread(manager.get_daily_data, code, None, None, max_days)
                return code, _enrich_ohlcv(raw, code, names[code], st_flags[code])
            except Exception as exc:  # noqa: BLE001 - per-symbol fetch failure degrades
                logger.warning("[probe] fetch failed for %s: %s", code, exc)
                return code, None

    results = await asyncio.gather(*[_fetch_one_guarded(c) for c in watchlist])
    for code, df in results:
        if df is None or df.empty:
            fetch_issues[code] = "fetch_failed_or_empty"
            continue
        frames[code] = df

    # ── run layer ────────────────────────────────────────────────────────
    from alphaevo.backtest.engine import BacktestEngine
    from alphaevo.strategy.dsl.parser import StrategyParser

    engine = BacktestEngine()
    parser = StrategyParser()
    contexts = _build_contexts(names, st_flags)

    tasks = [
        _run_one(spec, parser, engine, code, frames, contexts)
        for code in frames
        for spec in strategies
    ]

    ran: List[Tuple[str, str, Any]]
    try:
        ran = list(
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=False),
                timeout=timeout_seconds,
            )
        )
    except asyncio.TimeoutError:
        raise ProbeError(f"probe hard timeout after {timeout_seconds}s") from None

    per_symbol: Dict[str, Dict[str, Any]] = {
        code: {
            "name": names[code],
            "st": st_flags[code],
            "as_of_date": pd.Timestamp(df["date"].iloc[-1]).date().isoformat(),
            "signals": {},
            "strategy_errors": {},
            "error": fetch_issues.get(code, ""),
        }
        for code, df in frames.items()
    }
    # 取数失败的股票也要在 data_issues 里体现
    for code, issue in fetch_issues.items():
        if code not in per_symbol:
            per_symbol[code] = {"error": issue, "signals": {}, "strategy_errors": {}}

    for code, sid, outcome in ran:
        if isinstance(outcome, Exception):
            per_symbol[code]["strategy_errors"][sid] = f"{type(outcome).__name__}: {outcome}"
            continue
        per_symbol[code]["signals"][sid] = _signal_to_dict(outcome)

    return aggregate(per_symbol, cfg)


async def _run_one(
    spec: Dict[str, Any],
    parser: Any,
    engine: Any,
    code: str,
    frames: Dict[str, pd.DataFrame],
    contexts: Dict[str, Any],
) -> Tuple[str, str, Any]:
    """Evaluate one strategy×symbol pair; returns exception instead of raising."""
    try:
        strategy = await asyncio.to_thread(parser.parse_yaml, spec["content"])
        signal = await asyncio.to_thread(
            engine.signal_at_last_bar, strategy, frames[code], code, contexts
        )
        return code, spec["strategy_id"], signal
    except Exception as exc:  # noqa: BLE001 - per-strategy failure degrades
        return code, spec["strategy_id"], exc


def main() -> int:
    """CLI entry: python -m src.strategy_signal_probe [--symbols ...] [--output path.json]"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Strategy signal probe")
    parser.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR))
    parser.add_argument("--symbols", nargs="*", default=None, help="override watchlist")
    parser.add_argument("--output", default=None, help="write JSON to path")
    args = parser.parse_args()

    result = asyncio.run(
        run_probe(Path(args.config_dir), symbols=args.symbols)
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        logger.info("signal JSON written to %s", args.output)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())