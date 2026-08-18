# -*- coding: utf-8 -*-
"""数据源注册表发现与校验:实例数据(fetchers.yaml)+ 代码发现(import/health_check)。"""
from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from typing import Optional

from data_provider.specs import FetcherSpec, load_fetcher_specs

logger = logging.getLogger(__name__)


class FetcherRegistryError(RuntimeError):
    pass


def _env_missing(spec: FetcherSpec) -> list[str]:
    return [key for key in spec.env_required if not os.environ.get(key)]


def _resolve_health_check(spec: FetcherSpec) -> bool:
    """health_check 格式 'module:function',异常视为 False。"""
    raw = spec.health_check
    if not raw:
        return True
    module_name, _, func_name = raw.partition(":")
    if not func_name:
        return True
    try:
        module = importlib.import_module(module_name)
        func = getattr(module, func_name)
        return bool(func())
    except Exception:  # noqa: BLE001
        logger.warning("[registry] health_check failed for %s: %s", spec.name, raw)
        return False


def discover_fetchers(path: Optional[Path] = None) -> list[FetcherSpec]:
    """读注册表并校验;class 无法导入 fail-fast;env 缺失/health_check False 降级禁用。"""
    from data_provider.specs import DEFAULT_REGISTRY_PATH

    try:
        specs = load_fetcher_specs(path or DEFAULT_REGISTRY_PATH)
    except Exception as exc:  # noqa: BLE001
        raise FetcherRegistryError(
            f"fetcher registry load failed ({path or DEFAULT_REGISTRY_PATH}): {exc}"
        ) from exc
    discovered: list[FetcherSpec] = []
    for spec in specs:
        try:
            module = importlib.import_module(spec.module)
        except Exception as exc:  # noqa: BLE001
            raise FetcherRegistryError(
                f"fetcher {spec.name}: module {spec.module} import failed: {exc}"
            ) from exc
        fetcher_cls = getattr(module, spec.fetcher_class, None)
        if fetcher_cls is None or not isinstance(fetcher_cls, type):
            raise FetcherRegistryError(
                f"fetcher {spec.name}: class {spec.fetcher_class} not found in {spec.module}"
            )
        if _env_missing(spec):
            logger.warning("[registry] %s disabled: missing env %s", spec.name, _env_missing(spec))
            spec.enabled = False
        if spec.enabled and not _resolve_health_check(spec):
            logger.warning("[registry] %s disabled: health_check failed", spec.name)
            spec.enabled = False
        discovered.append(spec)
    return discovered
