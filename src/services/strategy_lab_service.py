# -*- coding: utf-8 -*-
"""Strategy Lab service: list / preview / backtest / evolve / publish.

The strategy lab is the interactive GUI counterpart of the daily signal
probe. It lets a user:

1. list deployed strategies (strategy_signals/*.yaml)
2. preview today's signals (reuses the probe machinery)
3. run a one-click backtest of a strategy against the watchlist
4. evolve a strategy (LLM / param search / hybrid) via alphaevo
5. publish a strategy YAML to the cloud fork via git
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src.strategy_signal_probe import (
    DEFAULT_CONFIG_DIR,
    _enrich_ohlcv,
    _warmup_days_for,
    load_probe_config,
    resolve_watchlist,
    run_probe,
)

logger = logging.getLogger(__name__)

LLM_KEY_ENV = ("ALPHAEVO_API_KEY", "LLM_DEEPSEEK_API_KEY")


def _alphaevo_exe() -> Path:
    """Resolve the alphaevo CLI executable.

    Defaults to <python-dir>/alphaevo(.exe) so the venv's console script is
    used; ALPHAEVO_EXE env var can override.
    """
    override = os.environ.get("ALPHAEVO_EXE")
    if override:
        return Path(override)
    exe = Path(sys.executable).parent / "alphaevo.exe"
    if not exe.exists():
        exe = Path(sys.executable).parent / "alphaevo"
    if not exe.exists():
        raise RuntimeError("alphaevo CLI not found; set ALPHAEVO_EXE")
    return exe


def _load_all_specs(config_dir: Path = DEFAULT_CONFIG_DIR) -> List[Dict[str, Any]]:
    """Load every strategy YAML (including disabled) from strategy_signals/.

    Each item: strategy_id / path / content / spec (raw yaml dict) /
    enabled / meta. Files that fail to parse are skipped.
    """
    specs: List[Dict[str, Any]] = []
    for yaml_path in sorted(Path(config_dir).glob("*.yaml")):
        if yaml_path.name == "config.yaml":
            continue
        try:
            with open(yaml_path, encoding="utf-8") as f:
                content = f.read()
            raw = yaml.safe_load(content)
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("[lab] skip unreadable strategy %s: %s", yaml_path.name, exc)
            continue
        if not isinstance(raw, dict):
            continue
        meta = raw.get("meta") or {}
        strategy_id = meta.get("id")
        if not strategy_id:
            continue
        specs.append(
            {
                "strategy_id": strategy_id,
                "path": yaml_path,
                "content": content,
                "spec": raw,
                "meta": meta,
                "enabled": not raw.get("disabled", False),
            }
        )
    return specs


def list_strategies(config_dir: Path = DEFAULT_CONFIG_DIR) -> List[Dict[str, Any]]:
    """List deployed strategies with metadata from strategy_signals/*.yaml."""
    items: List[Dict[str, Any]] = []
    for entry in _load_all_specs(config_dir):
        spec = entry["spec"]
        meta = entry["meta"]
        items.append(
            {
                "strategy_id": entry["strategy_id"],
                "family": meta.get("family", ""),
                "version": meta.get("version", 1),
                "name": meta.get("name", entry["strategy_id"]),
                "description": meta.get("description", ""),
                "warmup_days": spec.get("warmup_days", 120),
                "enabled": entry["enabled"],
                "disable_reason": spec.get("disable_reason", ""),
                "tie_rule": spec.get("tie_rule", "conservative"),
            }
        )
    return items


async def preview_signals(
    symbols: Optional[List[str]] = None,
    config_dir: Path = DEFAULT_CONFIG_DIR,
) -> Dict[str, Any]:
    """Run the daily signal probe and return the aggregated signal JSON."""
    return await run_probe(config_dir, symbols=symbols)


def _strategy_spec(
    strategy_id: str, config_dir: Path = DEFAULT_CONFIG_DIR
) -> Dict[str, Any]:
    for entry in _load_all_specs(config_dir):
        if entry["strategy_id"] == strategy_id:
            return entry
    raise ValueError(f"strategy not found: {strategy_id}")


async def run_lab_backtest(
    strategy_id: str,
    symbols: Optional[List[str]] = None,
    days: int = 360,
    config_dir: Path = DEFAULT_CONFIG_DIR,
) -> Dict[str, Any]:
    """One-click backtest: fetch watchlist frames -> alphaevo engine -> metrics."""
    from data_provider.base import DataFetcherManager

    spec = _strategy_spec(strategy_id, config_dir)
    cfg, _ = load_probe_config(config_dir)
    watchlist = symbols or resolve_watchlist(cfg)
    if not watchlist:
        raise ValueError("empty watchlist")

    manager = DataFetcherManager()
    warmup = _warmup_days_for(cfg, spec)
    max_days = days + max(60, warmup) + 30

    names: Dict[str, str] = {}
    st_flags: Dict[str, bool] = {}
    frames: Dict[str, Any] = {}
    issues: Dict[str, str] = {}
    for code in watchlist:
        try:
            raw_name = manager.get_stock_name(code, allow_realtime=False)
            name = str(raw_name) if raw_name else code
        except Exception as exc:  # noqa: BLE001 - name lookup must not fail the lab
            name = code
            logger.warning("[lab] name lookup failed for %s: %s", code, exc)
        names[code] = name
        st_flags[code] = "ST" in name or "*ST" in name
        try:
            raw, _ = await asyncio.to_thread(manager.get_daily_data, code, None, None, max_days)
            if raw is None or raw.empty:
                raise ValueError("empty frame")
            frames[code] = _enrich_ohlcv(raw, code, name, st_flags[code])
        except Exception as exc:  # noqa: BLE001 - per-symbol degrade
            issues[code] = f"{type(exc).__name__}: {exc}"
            logger.warning("[lab] fetch failed for %s: %s", code, exc)
    if not frames:
        raise ValueError("no data fetched for watchlist")

    from alphaevo.backtest.engine import BacktestEngine
    from alphaevo.evaluator.metrics import Evaluator
    from alphaevo.models.execution import SampleBatch
    from alphaevo.strategy.dsl.parser import StrategyParser

    strategy = await asyncio.to_thread(StrategyParser().parse_yaml, spec["content"])
    end = date.today()
    start = end - timedelta(days=days)
    batch = SampleBatch(
        batch_id=f"lab-{strategy_id}",
        strategy_id=strategy_id,
        symbols=sorted(frames.keys()),
        date_range=(start, end),
        requested_max_symbols=len(frames),
    )
    engine = BacktestEngine()
    result = await asyncio.to_thread(engine.run, strategy, frames, batch)
    report = await asyncio.to_thread(Evaluator().evaluate_fast, result, strategy)

    overall = report.overall
    per_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for sig in result.signals:
        per_symbol.setdefault(sig.symbol, []).append(
            {
                "signal_date": sig.signal_date.isoformat(),
                "direction": sig.direction.value if hasattr(sig.direction, "value") else str(sig.direction),
                "entry_price": sig.entry_price,
                "exit_price": sig.exit_price,
                "exit_date": sig.exit_date.isoformat() if sig.exit_date else None,
                "exit_reason": sig.exit_reason.value if hasattr(sig.exit_reason, "value") else str(sig.exit_reason),
                "return_pct": round(sig.return_pct, 4),
                "holding_days": sig.holding_days,
            }
        )
    for code in frames:
        per_symbol.setdefault(code, [])

    return {
        "strategy_id": strategy_id,
        "name": spec["meta"].get("name", strategy_id),
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "symbols": sorted(frames.keys()),
        "fetch_issues": issues,
        "overall": {
            "win_rate": overall.win_rate,
            "avg_return": overall.avg_return,
            "avg_win_return": overall.avg_win_return,
            "avg_loss_return": overall.avg_loss_return,
            "profit_loss_ratio": overall.profit_loss_ratio,
            "max_drawdown": overall.max_drawdown,
            "sharpe_ratio": overall.sharpe_ratio,
            "signal_count": overall.signal_count,
            "avg_holding_days": overall.avg_holding_days,
            "max_consecutive_loss": overall.max_consecutive_loss,
            "median_return": overall.median_return,
            "total_return": overall.total_return,
        },
        "confidence_score": report.confidence_score,
        "per_symbol": per_symbol,
        "by_regime": [
            {
                "regime": r.regime.value if hasattr(r.regime, "value") else str(r.regime),
                "win_rate": r.win_rate,
                "avg_return": r.avg_return,
                "signal_count": r.signal_count,
            }
            for r in report.by_regime
        ],
    }


def _llm_key_available() -> bool:
    return any(os.environ.get(key) for key in LLM_KEY_ENV)


def _alphaevo_env(dsa_root: Path) -> Dict[str, str]:
    """Merge env vars for the alphaevo subprocess.

    Loads ALPHAEVO_API_KEY from the alphaevo project .env when the current
    process env lacks it, and points ALPHAEVO_DSA_PATH at the DSA project so
    the dsa data adapter can import DataFetcherManager.
    """
    env = dict(os.environ)
    if not env.get("ALPHAEVO_API_KEY"):
        try:
            import alphaevo  # noqa: F401

            alphaevo_root = Path(alphaevo.__file__).resolve().parents[2]
            env_file = alphaevo_root / ".env"
            if env_file.is_file():
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        env.setdefault(key.strip(), value.strip())
        except Exception:  # noqa: BLE001 - key loading must not block evolution
            logger.warning("[lab] failed to read alphaevo .env for API key", exc_info=True)
    env.setdefault("ALPHAEVO_DSA_PATH", str(dsa_root))
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def run_evolution(
    strategy_id: str,
    method: str = "hybrid",
    rounds: int = 1,
    samples: int = 3,
    config_dir: Path = DEFAULT_CONFIG_DIR,
) -> Dict[str, Any]:
    """Evolve a strategy via the alphaevo CLI (llm/param_search/hybrid).

    LLM methods require ALPHAEVO_API_KEY or LLM_DEEPSEEK_API_KEY. The final
    strategy (family's newest version) is exported to strategy_signals/ so it
    can be published.
    """
    if method in ("llm", "hybrid") and not _llm_key_available():
        raise ValueError(
            "LLM evolution requires ALPHAEVO_API_KEY or LLM_DEEPSEEK_API_KEY; "
            "use method=param_search without a key"
        )
    spec = _strategy_spec(strategy_id, config_dir)
    family = spec["meta"].get("family") or strategy_id.rsplit("_v", 1)[0]

    from alphaevo.strategy.dsl.serializer import StrategySerializer
    from alphaevo.strategy.store import StrategyStore

    # The alphaevo store may hold a builtin/foreign strategy under the same id
    # (e.g. US-market versions from previous runs). Re-import the deployed A-share
    # YAML so the CLI evolves the right strategy.
    store = StrategyStore()
    store.import_from_file(spec["path"])

    output_dir = DEFAULT_CONFIG_DIR.parent / "reports" / f"lab_{family}_{date.today():%Y%m%d}"
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(_alphaevo_exe()),
        "evolve",
        strategy_id,
        "--method", method,
        "--rounds", str(rounds),
        "--samples", str(samples),
        "--adapter", "dsa",
        "--output", str(output_dir),
    ]
    logger.info("[lab] evolve cmd: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=3600,
        encoding="utf-8",
        errors="replace",
        env=_alphaevo_env(config_dir.parent),
    )
    if proc.returncode != 0:
        tail = proc.stderr[-2000:] or proc.stdout[-2000:]
        raise RuntimeError(f"evolution failed (rc={proc.returncode}): {tail}")

    store = StrategyStore()
    store.import_from_file(spec["path"])
    versions = store.list_by_family(family)
    if not versions:
        raise RuntimeError("evolution finished but no strategy found in store")
    champion = max(versions, key=lambda s: s.meta.version)

    exported: Optional[str] = None
    if champion.meta.id != strategy_id:
        target = config_dir / f"{family}_v{champion.meta.version}.yaml"
        serializer = StrategySerializer()
        serializer.to_file(champion, target)
        exported = target.name
        logger.info("[lab] exported evolved strategy to %s", target)

    return {
        "family": family,
        "source_strategy_id": strategy_id,
        "result_strategy_id": champion.meta.id,
        "version": champion.meta.version,
        "exported_yaml": exported,
        "report_dir": str(output_dir),
        "stdout_tail": proc.stdout[-1500:],
        "stderr_tail": proc.stderr[-1500:],
    }


def publish_strategies(
    strategy_ids: List[str],
    repo_root: Path,
    remote: str = "cloud",
    branch: str = "main",
) -> List[Dict[str, Any]]:
    """Commit the given strategy YAML files and push to the cloud fork.

    Reuses the existing git credentials (cloud remote URL carries the token).
    """
    if not strategy_ids:
        raise ValueError("no strategies selected for publish")
    repo_root = repo_root.resolve()
    config_dir = repo_root / "strategy_signals"

    paths = []
    for sid in strategy_ids:
        p = config_dir / f"{sid}.yaml"
        if not p.is_file():
            raise ValueError(f"strategy file missing: {p.name}")
        paths.append(p)

    changed = _git(repo_root, "status", "--porcelain", "--", *[str(p.relative_to(repo_root)) for p in paths])
    if not changed.strip():
        raise ValueError("no changes to publish (strategies already committed)")

    _git(repo_root, "add", "--", *[str(p.relative_to(repo_root)) for p in paths])
    _git(
        repo_root,
        "commit",
        "-m",
        f"feat(strategy): publish {' '.join(sid for sid in strategy_ids)}",
    )
    try:
        _git(repo_root, "push", remote, branch)
    except RuntimeError as exc:
        raise RuntimeError(f"push failed (sync with {remote} first): {exc}") from exc

    return [
        {
            "strategy_id": sid,
            "file": p.name,
            "committed": True,
            "pushed": True,
        }
        for sid, p in zip(strategy_ids, paths)
    ]


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr[-1500:]}")
    return proc.stdout
