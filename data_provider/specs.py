# -*- coding: utf-8 -*-
"""FetcherSpec 注册表模型与 YAML 加载(pydantic v2 校验)。"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ValidationError

from src.config import get_config

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "fetchers.yaml"


class FetcherSpecValidationError(ValueError):
    pass


class FetcherSpec(BaseModel):
    name: str
    module: str
    fetcher_class: str
    markets: list[str] = []
    capabilities: list[Literal["quote", "bar", "fundamental"]] = []
    priority: int = 99
    enabled: bool = True
    rate_limit: Optional[int] = None
    timeout: Optional[int] = None
    env_required: list[str] = []
    health_check: Optional[str] = None
    version: str = "1"


def load_fetcher_specs(path: Path) -> list[FetcherSpec]:
    """读 YAML 注册表,逐条校验后返回 FetcherSpec 列表。"""
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise FetcherSpecValidationError(f"{path}: YAML 顶层必须是映射")
    entries = data.get("fetchers", [])
    if not isinstance(entries, list):
        raise FetcherSpecValidationError(f"{path}: 缺少 fetchers 列表")
    specs: list[FetcherSpec] = []
    for index, entry in enumerate(entries):
        try:
            specs.append(FetcherSpec.model_validate(entry))
        except ValidationError as exc:
            raise FetcherSpecValidationError(f"{path}[{index}]: {exc}") from exc
    return specs
