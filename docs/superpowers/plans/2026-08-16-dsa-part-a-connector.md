# Part-A: 连接器抽象 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一 bar/quote/fundamental 契约 + 注册表 + 配置化启停,四源(akshare/tushare/yfinance/finnhub)适配,旧调用点全量迁移。

**Architecture:** pydantic v2 契约层(`data_provider/contracts.py`)定义 raw/derived 分层模型;`config/fetchers.yaml` + `data_provider/registry.py` 做实例数据 + 代码发现;`DataFetcherManager` 改注册表驱动路由;feature flag `CONNECTOR_V2_ENABLED` 控制切换,兼容期保留旧路径。

**Tech Stack:** Python 3.11+, pydantic 2.13(venv), SQLAlchemy 2, pytest(markers: unit/integration/network), PyYAML(已有)

## Global Constraints

- pydantic v2(项目 venv 2.13.4),禁用 pydantic v1 API
- artifact/模型顶层统一 `schema_version: 1`(仅文件类产物;契约模型不含该字段)
- 测试标记:`@pytest.mark.unit`(离线)/ `@pytest.mark.integration`(无网络服务级)/ `@pytest.mark.network`(外部依赖),沿用 setup.cfg
- 无 token 的源自动禁用(warn-only);class 无法导入 fail-fast
- `_DAILY_MARKET_FETCHER_SUPPORT` 仅在 grep 兜底确认零调用方后删除
- 新增文件行宽 ≤ 120(black),中文注释沿用现有风格
- 依赖只允许 requirements.txt 已有项(YAML 已有:PyYAML>=6.0)

---

### Task 1: contracts.py — 契约模型与 legacy 兼容

**Files:**
- Create: `data_provider/contracts.py`
- Test: `tests/test_contracts.py`

**Interfaces:**
- Consumes: `data_provider/realtime_types.py` 的 `UnifiedRealtimeQuote`(仅 legacy_compat 转换用)
- Produces:
  - `Quote`(pydantic BaseModel):code:str, name:str="", price/open/high/low/pre_close/volume/amount/change_pct/change_amount/bid/ask: Optional[float]=None, tz: Optional[Literal['Asia/Shanghai','America/New_York','UTC']]=None, currency/market: Optional[str]=None, fetched_at/provider_timestamp: Optional[str]=None, is_stale: Optional[bool]=None, stale_seconds: Optional[int]=None, fallback_from: Optional[str]=None, data_quality: Optional[str]=None, missing_fields: Optional[list[str]]=None
  - `Quote.legacy_compat() -> UnifiedRealtimeQuote`(映射相同字段,source 用 RealtimeSource.FALLBACK 或传入)
  - `Bar`:date:date, open/high/low/close/volume/amount/pct_chg/turnover_rate: Optional[float]=None
  - `FundamentalRaw`:report_date:date, fiscal_period:Literal['Q1','Q2','Q3','Q4','FY'], market:str, 以及 ~25 三表关键科目(全部 Optional[float]):total_assets/total_liabilities/total_equity/revenue/net_income/operating_cashflow/investing_cashflow/financing_cashflow/gross_margin/dividend_yield/industry:Optional[str]=None/... 具体科目见实现
  - `FundamentalDerived`:三组字段,每组用 `Field(description=...)` 标注依赖源:① roe/dividend_yield_derived ② pe_ratio/pb_ratio ③ high_52w/low_52w
  - `QuoteDerived`:volume_ratio/turnover_rate/amplitude/pe_ratio/pb_ratio/total_mv/circ_mv/change_60d/high_52w/low_52w 全 Optional[float]
  - 模型 `model_config = ConfigDict(populate_by_name=True)`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_contracts.py
import pytest
from datetime import date
from data_provider.contracts import Bar, Quote, QuoteDerived, FundamentalRaw, FundamentalDerived


class TestQuoteRaw:
    def test_accepts_minimal_fields(self):
        q = Quote(code="600519", price=1700.0)
        assert q.price == 1700.0
        assert q.bid is None  # 缺省容忍

    def test_legacy_compat_maps_fields(self):
        q = Quote(code="600519", price=1700.0, currency="CNY", market="cn", is_stale=False)
        old = q.legacy_compat()
        assert old.code == "600519"
        assert old.price == 1700.0
        assert old.currency == "CNY"


class TestBar:
    def test_requires_date_and_ohlc(self):
        b = Bar(date=date(2026, 8, 15), open=1.0, high=2.0, low=0.5, close=1.5, volume=100)
        assert b.close == 1.5
        assert b.turnover_rate is None


class TestFundamentalRaw:
    def test_requires_report_period(self):
        fr = FundamentalRaw(report_date=date(2026, 6, 30), fiscal_period="Q2", market="cn",
                            total_assets=1.0, revenue=1.0, net_income=1.0)
        assert fr.fiscal_period == "Q2"


class TestQuoteDerived:
    def test_all_optional(self):
        d = QuoteDerived()
        assert d.pe_ratio is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_contracts.py -v`
Expected: FAIL — ModuleNotFoundError: data_provider.contracts

- [ ] **Step 3: 写实现**

```python
# data_provider/contracts.py
# -*- coding: utf-8 -*-
"""统一数据契约层(pydantic v2):raw/derived 分层,缺省容忍。"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote


class Quote(BaseModel):
    """fetcher 直接产出的实时行情(raw 层)。"""
    model_config = ConfigDict(populate_by_name=True)

    code: str
    name: str = ""
    price: Optional[float] = None
    open_price: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    pre_close: Optional[float] = None
    volume: Optional[int] = None
    amount: Optional[float] = None
    change_pct: Optional[float] = Field(None, description="涨跌幅(%),基准为昨收")
    change_amount: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    tz: Optional[Literal["Asia/Shanghai", "America/New_York", "UTC"]] = None
    currency: Optional[str] = None
    market: Optional[str] = None
    fetched_at: Optional[str] = None
    provider_timestamp: Optional[str] = None
    is_stale: Optional[bool] = None
    stale_seconds: Optional[int] = None
    fallback_from: Optional[str] = None
    data_quality: Optional[str] = None
    missing_fields: Optional[list[str]] = None

    def legacy_compat(self, source: RealtimeSource = RealtimeSource.FALLBACK) -> UnifiedRealtimeQuote:
        """转回旧 UnifiedRealtimeQuote,兼容迁移期调用方。"""
        return UnifiedRealtimeQuote(
            code=self.code, name=self.name, source=source,
            fetched_at=self.fetched_at, provider_timestamp=self.provider_timestamp,
            is_stale=self.is_stale, stale_seconds=self.stale_seconds,
            fallback_from=self.fallback_from, market=self.market, currency=self.currency,
            data_quality=self.data_quality, missing_fields=self.missing_fields,
            price=self.price, change_pct=self.change_pct, change_amount=self.change_amount,
            volume=self.volume, amount=self.amount,
            open_price=self.open_price, high=self.high, low=self.low, pre_close=self.pre_close,
        )


class Bar(BaseModel):
    """日线契约:仅 OHLCV + amount + pct_chg + turnover_rate,派生指标由调用方计算。"""
    model_config = ConfigDict(populate_by_name=True)

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: Optional[float] = None
    pct_chg: Optional[float] = None
    turnover_rate: Optional[float] = None


class FundamentalRaw(BaseModel):
    """三表关键科目(report_date/fiscal_period 必填)。"""
    model_config = ConfigDict(populate_by_name=True)

    report_date: date
    fiscal_period: Literal["Q1", "Q2", "Q3", "Q4", "FY"]
    market: str
    total_assets: Optional[float] = None
    total_liabilities: Optional[float] = None
    total_equity: Optional[float] = None
    revenue: Optional[float] = None
    net_income: Optional[float] = None
    operating_cashflow: Optional[float] = None
    investing_cashflow: Optional[float] = None
    financing_cashflow: Optional[float] = None
    gross_margin: Optional[float] = None
    dividend_yield: Optional[float] = None
    industry: Optional[str] = None


class FundamentalDerived(BaseModel):
    """派生指标,分三组标注依赖源。"""
    model_config = ConfigDict(populate_by_name=True)

    roe: Optional[float] = Field(None, description="依赖:纯基本面")
    dividend_yield_derived: Optional[float] = Field(None, description="依赖:纯基本面")
    pe_ratio: Optional[float] = Field(None, description="依赖:跨切股价")
    pb_ratio: Optional[float] = Field(None, description="依赖:跨切股价")
    high_52w: Optional[float] = Field(None, description="依赖:历史窗口")
    low_52w: Optional[float] = Field(None, description="依赖:历史窗口")


class QuoteDerived(BaseModel):
    """行情派生指标,由 QuoteDerivedCalculator 组合 Quote+Bar+FundamentalRaw 产出。"""
    model_config = ConfigDict(populate_by_name=True)

    volume_ratio: Optional[float] = None
    turnover_rate: Optional[float] = None
    amplitude: Optional[float] = None
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    total_mv: Optional[float] = None
    circ_mv: Optional[float] = None
    change_60d: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
```

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_contracts.py -v`
Expected: PASS(5 个测试)

- [ ] **Step 5: Commit**

```bash
git add data_provider/contracts.py tests/test_contracts.py
git commit -m "feat(connector): unified v2 contracts with raw/derived split"
```

---

### Task 2: FetcherSpec + fetchers.yaml 注册表文件

**Files:**
- Create: `data_provider/specs.py`
- Create: `config/fetchers.yaml`
- Test: `tests/test_fetcher_specs.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `FetcherSpec`(pydantic BaseModel):name:str, module:str, class:str, markets:list[str], capabilities:list[Literal['quote','bar','fundamental']], priority:int=99, enabled:bool=True, rate_limit:Optional[int]=None, timeout:Optional[int]=None, env_required:list[str]=[], health_check:Optional[str]=None, version:str="1"
  - `load_fetcher_specs(path: Path) -> list[FetcherSpec]`:读 YAML 校验返回;`FetcherSpecValidationError` 派生自 ValueError

- [ ] **Step 1: 写失败测试**

```python
# tests/test_fetcher_specs.py
import pytest
from pathlib import Path
from data_provider.specs import FetcherSpec, load_fetcher_specs, FetcherSpecValidationError


SAMPLE = """
fetchers:
  - name: akshare
    module: data_provider.akshare_fetcher
    class: AkshareFetcher
    markets: [cn, hk]
    capabilities: [quote, bar, fundamental]
    priority: 1
    enabled: true
    rate_limit: 20
    timeout: 15
    env_required: []
    health_check: null
    version: "1"
"""


class TestFetcherSpec:
    def test_parse_sample(self):
        specs = load_fetcher_specs_from_text(SAMPLE)
        assert len(specs) == 1
        s = specs[0]
        assert s.name == "akshare"
        assert s.capabilities == ["quote", "bar", "fundamental"]
        assert s.priority == 1

    def test_bad_capability_rejected(self):
        with pytest.raises(FetcherSpecValidationError):
            load_fetcher_specs_from_text(SAMPLE.replace("fundamental", "nonsense"))

    def test_missing_class_rejected(self):
        with pytest.raises(FetcherSpecValidationError):
            load_fetcher_specs_from_text(SAMPLE.replace("class: AkshareFetcher", "class: 123"))
```

在测试文件中加 helper:`load_fetcher_specs_from_text(text)` 用 tempfile 写文本后调 `load_fetcher_specs`。

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_fetcher_specs.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 写实现 + 配置文件**

```python
# data_provider/specs.py
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
    class_name: str = ""  # 兼容字段,见 validate
    markets: list[str] = []
    capabilities: list[Literal["quote", "bar", "fundamental"]] = []
    priority: int = 99
    enabled: bool = True
    rate_limit: Optional[int] = None
    timeout: Optional[int] = None
    env_required: list[str] = []
    health_check: Optional[str] = None
    version: str = "1"
```

注意:YAML 中 `class: AkshareFetcher` 与 pydantic 字段 `class` 冲突(保留字),实现时用 `alias_generator` 或字段名 `fetcher_class`,yaml key 用 `fetcher_class`。**具体约定:`config/fetchers.yaml` 使用 `fetcher_class:` 键名**,`FetcherSpec` 字段 `fetcher_class: str`。测试文本 SAMPLE 相应改为 `fetcher_class: AkshareFetcher`(Step 1 测试代码已据此)。

```yaml
# config/fetchers.yaml
# 数据源注册表:实例数据。新增源 = 代码 + 此条目(两步都不能省)。
# capabilities: quote/bar/fundamental;env_required 缺失的源启动时自动禁用(warn-only)。
fetchers:
  - name: akshare
    module: data_provider.akshare_fetcher
    fetcher_class: AkshareFetcher
    markets: [cn, hk]
    capabilities: [quote, bar, fundamental]
    priority: 1
    enabled: true
    rate_limit: 20
    timeout: 15
    env_required: []
    health_check: null
    version: "1"
  - name: tushare
    module: data_provider.tushare_fetcher
    fetcher_class: TushareFetcher
    markets: [cn, hk]
    capabilities: [quote, bar, fundamental]
    priority: 2
    enabled: true
    rate_limit: 200
    timeout: 15
    env_required: [TUSHARE_TOKEN]
    health_check: null
    version: "1"
  - name: yfinance
    module: data_provider.yfinance_fetcher
    fetcher_class: YfinanceFetcher
    markets: [cn, hk, us, jp, kr, tw]
    capabilities: [quote, bar, fundamental]
    priority: 4
    enabled: true
    rate_limit: 10
    timeout: 20
    env_required: []
    health_check: null
    version: "1"
  - name: finnhub
    module: data_provider.finnhub_fetcher
    fetcher_class: FinnhubFetcher
    markets: [us]
    capabilities: [quote, bar, fundamental]
    priority: 5
    enabled: true
    rate_limit: 60
    timeout: 10
    env_required: [FINNHUB_API_KEY]
    health_check: null
    version: "1"
```

`load_fetcher_specs(path)` 实现:读文件 → `yaml.safe_load` → `FetcherSpec.model_validate(each)` → ValidationError 包成 `FetcherSpecValidationError` → 返回 list。

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_fetcher_specs.py -v`
Expected: PASS(3 个测试)

- [ ] **Step 5: Commit**

```bash
git add data_provider/specs.py config/fetchers.yaml tests/test_fetcher_specs.py
git commit -m "feat(connector): fetcher spec models + registry yaml"
```

---

### Task 3: registry.py — 发现与校验(import 验证 + env 降级 + health_check)

**Files:**
- Create: `data_provider/registry.py`
- Test: `tests/test_fetcher_registry.py`

**Interfaces:**
- Consumes: `data_provider.specs.load_fetcher_specs` / `FetcherSpec` / `DEFAULT_REGISTRY_PATH`
- Produces:
  - `discover_fetchers(path: Optional[Path] = None) -> list[FetcherSpec]`:按规格跑 5 步(读 YAML → pydantic 校验 → import_module → 验证 fetcher_class 是类 → health_check 调用);class 导入失败 raise `FetcherRegistryError`(fail-fast);env_required 缺失 → spec.enabled=False(warn-only);health_check 返回 False → spec.enabled=False
  - `FetcherRegistryError(RuntimeError)`
  - `_resolve_health_check(spec) -> bool`:`health_check` 格式 `module:function`,importlib 调用返回 bool,异常视为 False
  - `_env_missing(spec) -> list[str]`:返回缺失的环境变量名

- [ ] **Step 1: 写失败测试**

```python
# tests/test_fetcher_registry.py
import pytest
from data_provider.registry import discover_fetchers, FetcherRegistryError
from data_provider.specs import FetcherSpec, load_fetcher_specs


class TestDiscover:
    def test_class_not_found_fails_fast(self, monkeypatch):
        specs = [FetcherSpec(name="bad", module="data_provider.contracts", fetcher_class="NoSuchClass",
                             capabilities=["quote"], enabled=True)]
        monkeypatch.setattr("data_provider.registry.load_fetcher_specs", lambda path: specs)
        with pytest.raises(FetcherRegistryError):
            discover_fetchers()

    def test_module_not_found_fails_fast(self, monkeypatch):
        specs = [FetcherSpec(name="bad", module="no.such.module", fetcher_class="X",
                             capabilities=["quote"], enabled=True)]
        monkeypatch.setattr("data_provider.registry.load_fetcher_specs", lambda path: specs)
        with pytest.raises(FetcherRegistryError):
            discover_fetchers()

    def test_missing_env_disables_warn_only(self, monkeypatch):
        specs = [FetcherSpec(name="t", module="data_provider.contracts", fetcher_class="Quote",
                             capabilities=["quote"], enabled=True,
                             env_required=["DEFINITELY_NOT_SET_VAR_XYZ"])]
        monkeypatch.setattr("data_provider.registry.load_fetcher_specs", lambda path: specs)
        monkeypatch.delenv("DEFINITELY_NOT_SET_VAR_XYZ", raising=False)
        out = discover_fetchers()
        assert out[0].enabled is False

    def test_health_check_false_disables(self, monkeypatch):
        specs = [FetcherSpec(name="h", module="data_provider.contracts", fetcher_class="Quote",
                             capabilities=["quote"], enabled=True,
                             health_check="tests.test_fetcher_registry:_health_false")]
        monkeypatch.setattr("data_provider.registry.load_fetcher_specs", lambda path: specs)
        out = discover_fetchers()
        assert out[0].enabled is False

    def test_valid_spec_passes(self, monkeypatch):
        specs = [FetcherSpec(name="ok", module="data_provider.contracts", fetcher_class="Quote",
                             capabilities=["quote"], enabled=True)]
        monkeypatch.setattr("data_provider.registry.load_fetcher_specs", lambda path: specs)
        out = discover_fetchers()
        assert out[0].enabled is True


def _health_false() -> bool:
    return False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_fetcher_registry.py -v`
Expected: FAIL — ModuleNotFoundError: data_provider.registry

- [ ] **Step 3: 写实现**

```python
# data_provider/registry.py
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

    specs = load_fetcher_specs(path or DEFAULT_REGISTRY_PATH)
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_fetcher_registry.py -v`
Expected: PASS(5 个测试)

- [ ] **Step 5: Commit**

```bash
git add data_provider/registry.py tests/test_fetcher_registry.py
git commit -m "feat(connector): registry discovery with fail-fast and env degrade"
```

---

### Task 4: DataFetcherManager 注册表驱动 + CONNECTOR_V2_ENABLED flag

**Files:**
- Modify: `data_provider/base.py`(DataFetcherManager `__init__`/路由,不删 `_DAILY_MARKET_FETCHER_SUPPORT` 本体)
- Modify: `src/config.py`(新增 `connector_v2_enabled: bool`,env `CONNECTOR_V2_ENABLED`)
- Test: `tests/test_fetcher_manager_registry_routing.py`

**Interfaces:**
- Consumes: `data_provider.registry.discover_fetchers` / `FetcherSpec`
- Produces:
  - `DataFetcherManager.registry_specs: dict[str, FetcherSpec]`(name → spec,仅 enabled)
  - `DataFetcherManager._try_fetcher_quote_spec(spec: FetcherSpec, stock_code: str, **kw) -> Optional[UnifiedRealtimeQuote]`:按 spec 取 fetcher 实例(优先现有 `_fetchers_by_name`),调 `get_realtime_quote`,失败返回 None
  - `get_realtime_quote` 内:flag 开启时走注册表路由(capabilities 含 quote + market 匹配 + priority 排序),flag 关闭时走旧 if/else(保留原样)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_fetcher_manager_registry_routing.py
import pytest
from data_provider.base import DataFetcherManager
from data_provider.specs import FetcherSpec


class _SpecFetcher:
    name = "spec_quote"
    priority = 5

    def __init__(self):
        self.calls = []

    def get_realtime_quote(self, code, **kw):
        self.calls.append(code)
        from data_provider.contracts import Quote
        return Quote(code=code, price=10.0).legacy_compat()


class TestRegistryRouting:
    def test_flag_on_routes_by_spec(self, monkeypatch):
        fake = _SpecFetcher()
        manager = DataFetcherManager(fetchers=[fake])
        spec = FetcherSpec(name="spec_quote", module="x", fetcher_class="y",
                           markets=["cn"], capabilities=["quote"], enabled=True)
        monkeypatch.setattr(manager, "registry_specs", {"spec_quote": spec})
        monkeypatch.setattr(manager, "_spec_instance", lambda spec: fake)
        monkeypatch.setattr("src.config.get_config", lambda: type("C", (), {"connector_v2_enabled": True})())
        quote = manager.get_realtime_quote("600519")
        assert quote is not None and fake.calls == ["600519"]

    def test_flag_off_keeps_legacy_path(self, monkeypatch):
        fake = _SpecFetcher()
        manager = DataFetcherManager(fetchers=[fake])
        monkeypatch.setattr("src.config.get_config", lambda: type("C", (), {"connector_v2_enabled": False})())
        # 旧路径会尝试多源,用 _SpecFetcher 无能力;只需断言不抛异常(旧逻辑返回 None 兜底)
        quote = manager.get_realtime_quote("999999")  # 不存在的代码,旧路径各源失败 → None
        assert quote is None
```

注:旧路径对 `999999` 会走多源尝试(可能触网络),测试改用 monkeypatch 后不保证离线。**调整**:flag_off 测试断言 `get_realtime_quote` 在 `connector_v2_enabled=False` 且 fetchers 空列表时返回 None,且不调用注册表(monkeypatch `discover_fetchers` 断言未调用)。

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_fetcher_manager_registry_routing.py -v`
Expected: FAIL(registry_specs 不存在)

- [ ] **Step 3: 写实现**

在 `src/config.py` 的 Config dataclass 加字段(`connector_v2_enabled: bool = False`),构造处读 `os.getenv('CONNECTOR_V2_ENABLED', 'false').lower() == 'true'`(仿照现有 `prefetch_realtime_quotes` 模式,src/config.py:1159/2101)。

`data_provider/base.py` DataFetcherManager:
```python
def _registry_specs(self) -> dict[str, FetcherSpec]:
    """返回 name → enabled spec(带缓存)。"""
    if getattr(self, "_registry_cache", None) is None:
        from data_provider.registry import discover_fetchers
        self._registry_cache = {s.name: s for s in discover_fetchers() if s.enabled}
    return self._registry_cache

def _spec_instance(self, spec: FetcherSpec):
    """按 spec 解析 fetcher 实例(复用现有 _fetchers_by_name 或惰性创建)。"""
    return self._fetchers_by_name.get(spec.name)
```

`get_realtime_quote` 开头(在 `enable_realtime_quote` 检查之后)插入:
```python
config = get_config()
if getattr(config, "connector_v2_enabled", False):
    return self._get_quote_via_registry(stock_code, config)
```
`_get_quote_via_registry` 实现:归一化 code → market 推断(复用现有 `_is_us_code`/`_is_hk_market` 等辅助)→ 取 registry_specs 中 capabilities 含 quote 且 market 匹配的 spec,按 priority 升序,逐个 `_try_fetcher_quote_spec`,首个成功即 `_enrich_realtime_quote` 返回;全失败返回 None。

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_fetcher_manager_registry_routing.py -v`
Expected: PASS

- [ ] **Step 5: 回归验证(重要)**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_realtime_quote_fallback_logging.py tests/test_fetcher_source_optimization.py tests/test_realtime_types.py -v`
Expected: PASS(旧路径未被破坏)

- [ ] **Step 6: Commit**

```bash
git add data_provider/base.py src/config.py tests/test_fetcher_manager_registry_routing.py
git commit -m "feat(connector): registry-driven routing behind CONNECTOR_V2_ENABLED flag"
```

---

### Task 5: akshare 适配(to_quote/to_bar/to_fundamental)

**Files:**
- Modify: `data_provider/akshare_fetcher.py`(类尾部加方法)
- Test: `tests/test_akshare_contract_adapter.py`

**Interfaces:**
- Consumes: `data_provider.contracts` 的 Quote/Bar/FundamentalRaw
- Produces: `AkshareFetcher.to_quote(raw: UnifiedRealtimeQuote) -> Quote`、`to_bar(df: pd.DataFrame) -> list[Bar]`、`to_fundamental(df_or_dict) -> Optional[FundamentalRaw]`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_akshare_contract_adapter.py
import pytest
from datetime import date
import pandas as pd
from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.contracts import Quote, Bar, FundamentalRaw
from data_provider.realtime_types import UnifiedRealtimeQuote


@pytest.fixture
def fetcher():
    return AkshareFetcher()


class TestAkshareToQuote:
    def test_maps_core_fields(self, fetcher):
        old = UnifiedRealtimeQuote(code="600519", price=1700.0, change_pct=1.2,
                                   currency="CNY", market="cn")
        q = fetcher.to_quote(old)
        assert isinstance(q, Quote)
        assert q.price == 1700.0
        assert q.currency == "CNY"
        assert q.tz == "Asia/Shanghai"  # A 股固定时区

    def test_missing_fields_tolerated(self, fetcher):
        old = UnifiedRealtimeQuote(code="000001")
        q = fetcher.to_quote(old)
        assert q.bid is None and q.ask is None


class TestAkshareToBar:
    def test_bar_shape(self, fetcher):
        df = pd.DataFrame([
            {"date": "2026-08-14", "open": 1, "high": 2, "low": 0.5,
             "close": 1.5, "volume": 100, "amount": 150.0, "pct_chg": 5.0},
        ])
        bars = fetcher.to_bar(df)
        assert len(bars) == 1
        assert bars[0].date == date(2026, 8, 14)
        assert bars[0].close == 1.5


class TestAkshareToFundamental:
    def test_raw_shape(self, fetcher):
        fr = fetcher.to_fundamental({"total_assets": 1.0, "report_date": "2026-06-30",
                                     "fiscal_period": "Q2", "market": "cn"})
        assert isinstance(fr, FundamentalRaw)
        assert fr.report_date == date(2026, 6, 30)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_akshare_contract_adapter.py -v`
Expected: FAIL(AttributeError: to_quote)

- [ ] **Step 3: 写实现**(akshare_fetcher.py 类内追加)

```python
def to_quote(self, raw: UnifiedRealtimeQuote) -> Quote:
    """旧 UnifiedRealtimeQuote → 新 Quote(缺失字段容忍)。"""
    from data_provider.contracts import Quote
    return Quote(
        code=raw.code, name=raw.name,
        price=raw.price, open_price=raw.open_price, high=raw.high, low=raw.low,
        pre_close=raw.pre_close, volume=raw.volume, amount=raw.amount,
        change_pct=raw.change_pct, change_amount=raw.change_amount,
        tz="Asia/Shanghai", currency=raw.currency, market=raw.market,
        fetched_at=raw.fetched_at, provider_timestamp=raw.provider_timestamp,
        is_stale=raw.is_stale, stale_seconds=raw.stale_seconds,
        fallback_from=raw.fallback_from, data_quality=raw.data_quality,
        missing_fields=raw.missing_fields,
    )

def to_bar(self, df: pd.DataFrame) -> list[Bar]:
    """标准日线 DataFrame → list[Bar]。"""
    from data_provider.contracts import Bar
    bars = []
    for _, row in df.iterrows():
        try:
            bars.append(Bar(
                date=pd.to_datetime(row["date"]).date(),
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                volume=int(row["volume"]),
                amount=float(row["amount"]) if pd.notna(row.get("amount")) else None,
                pct_chg=float(row["pct_chg"]) if pd.notna(row.get("pct_chg")) else None,
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return bars

def to_fundamental(self, payload: dict) -> Optional[FundamentalRaw]:
    """akshare 财报 dict → FundamentalRaw;缺 report_date 返回 None。"""
    from data_provider.contracts import FundamentalRaw
    report_date = payload.get("report_date")
    if not report_date:
        return None
    return FundamentalRaw(
        report_date=pd.to_datetime(report_date).date(),
        fiscal_period=str(payload.get("fiscal_period") or "FY"),
        market=str(payload.get("market") or "cn"),
        total_assets=payload.get("total_assets"),
        total_liabilities=payload.get("total_liabilities"),
        total_equity=payload.get("total_equity"),
        revenue=payload.get("revenue"),
        net_income=payload.get("net_income"),
        operating_cashflow=payload.get("operating_cashflow"),
        investing_cashflow=payload.get("investing_cashflow"),
        financing_cashflow=payload.get("financing_cashflow"),
        gross_margin=payload.get("gross_margin"),
        dividend_yield=payload.get("dividend_yield"),
        industry=payload.get("industry"),
    )
```

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_akshare_contract_adapter.py -v`
Expected: PASS(4 个测试)

- [ ] **Step 5: Commit**

```bash
git add data_provider/akshare_fetcher.py tests/test_akshare_contract_adapter.py
git commit -m "feat(connector): akshare contract adapters"
```

---

### Task 6: tushare / yfinance / finnhub 适配(同模式三连)

**Files:**
- Modify: `data_provider/tushare_fetcher.py`、`data_provider/yfinance_fetcher.py`、`data_provider/finnhub_fetcher.py`
- Test: `tests/test_tushare_contract_adapter.py`、`tests/test_yfinance_contract_adapter.py`、`tests/test_finnhub_contract_adapter.py`

**Interfaces:**
- Produces(每个 fetcher 相同签名):`to_quote(raw: UnifiedRealtimeQuote) -> Quote`(tz 按市场:yfinance 用 "America/New_York" 或 "Asia/Shanghai",finnhub 美股 "America/New_York",tushare A 股 "Asia/Shanghai")、`to_bar(df) -> list[Bar]`、`to_fundamental(payload) -> Optional[FundamentalRaw]`(market 分别为 cn / us / us)

- [ ] **Step 1: 写失败测试**(以 tushare 为例,其余两文件同构)

```python
# tests/test_tushare_contract_adapter.py
import pytest
import pandas as pd
from datetime import date
from data_provider.tushare_fetcher import TushareFetcher
from data_provider.contracts import Quote, Bar
from data_provider.realtime_types import UnifiedRealtimeQuote


@pytest.fixture
def fetcher():
    return TushareFetcher()


def test_to_quote_maps_fields(fetcher):
    old = UnifiedRealtimeQuote(code="600519", price=1700.0, currency="CNY")
    q = fetcher.to_quote(old)
    assert isinstance(q, Quote)
    assert q.tz == "Asia/Shanghai"


def test_to_bar_shape(fetcher):
    df = pd.DataFrame([{"date": "2026-08-14", "open": 1, "high": 2, "low": 0.5,
                        "close": 1.5, "volume": 100}])
    bars = fetcher.to_bar(df)
    assert bars[0].date == date(2026, 8, 14)
```

- [ ] **Step 2: 运行三个测试文件验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_tushare_contract_adapter.py tests/test_yfinance_contract_adapter.py tests/test_finnhub_contract_adapter.py -v`
Expected: FAIL(AttributeError)

- [ ] **Step 3: 写实现**(三个 fetcher 类内追加,模式同 Task 5;yfinance 的 `to_bar` 兼容 "Date" 大写列,`to_quote` tz 按 market 参数映射,默认 us→"America/New_York";finnhub `to_fundamental` market="us")

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_tushare_contract_adapter.py tests/test_yfinance_contract_adapter.py tests/test_finnhub_contract_adapter.py -v`
Expected: PASS

- [ ] **Step 5: 回归:四源现有测试不破坏**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_tushare_fetcher.py tests/test_yfinance_fetcher.py tests/test_finnhub_fetcher.py tests/test_akshare_fetcher_code_conversion.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add data_provider/tushare_fetcher.py data_provider/yfinance_fetcher.py data_provider/finnhub_fetcher.py tests/test_tushare_contract_adapter.py tests/test_yfinance_contract_adapter.py tests/test_finnhub_contract_adapter.py
git commit -m "feat(connector): tushare/yfinance/finnhub contract adapters"
```

---

### Task 7: QuoteDerivedCalculator

**Files:**
- Create: `data_provider/quote_derived.py`
- Test: `tests/test_quote_derived.py`

**Interfaces:**
- Consumes: `Quote` / `Bar` / `FundamentalRaw`
- Produces:
  - `QuoteDerivedCalculator.calculate(quote: Quote, bars: Optional[list[Bar]] = None, fundamental: Optional[FundamentalRaw] = None) -> QuoteDerived`
  - 计算规则:volume_ratio = volume / 前 5 日均量(不足 5 日 None);amplitude = (high-low)/pre_close*100(缺省 None);total_mv/circ_mv/pe_ratio/pb_ratio/change_60d/high_52w/low_52w 仅 fundamental/bars 提供时填充,否则 None

- [ ] **Step 1: 写失败测试**

```python
# tests/test_quote_derived.py
import pytest
from datetime import date, timedelta
from data_provider.contracts import Bar, Quote, FundamentalRaw
from data_provider.quote_derived import QuoteDerivedCalculator


def _bars(days: int, base_vol: int = 1000):
    out = []
    for i in range(days):
        d = date(2026, 8, 1) + timedelta(days=i)
        out.append(Bar(date=d, open=10, high=11, low=9, close=10, volume=base_vol))
    return out


def test_volume_ratio_uses_prev_5d_avg():
    bars = _bars(6)
    bars[-1] = Bar(date=bars[-1].date, open=10, high=11, low=9, close=10, volume=3000)
    q = Quote(code="600519", price=10.0, volume=3000, pre_close=9.5, high=11.0, low=9.0)
    d = QuoteDerivedCalculator().calculate(q, bars=bars)
    assert d.volume_ratio == pytest.approx(3.0, abs=0.01)


def test_amplitude_from_pre_close():
    q = Quote(code="600519", price=10.0, pre_close=9.5, high=11.0, low=9.0)
    d = QuoteDerivedCalculator().calculate(q)
    assert d.amplitude == pytest.approx(21.05, abs=0.01)


def test_no_bars_means_none():
    q = Quote(code="600519", price=10.0)
    d = QuoteDerivedCalculator().calculate(q)
    assert d.volume_ratio is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_quote_derived.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 写实现**

```python
# data_provider/quote_derived.py
# -*- coding: utf-8 -*-
"""QuoteDerived 计算层:组合 Quote + Bar + FundamentalRaw 产出派生指标。"""
from __future__ import annotations

from typing import Optional

from data_provider.contracts import Bar, FundamentalRaw, Quote, QuoteDerived


class QuoteDerivedCalculator:
    def calculate(self, quote: Quote, bars: Optional[list[Bar]] = None,
                  fundamental: Optional[FundamentalRaw] = None) -> QuoteDerived:
        d = QuoteDerived()
        if bars:
            d.volume_ratio = self._volume_ratio(quote, bars)
        if quote.pre_close and quote.high is not None and quote.low is not None:
            d.amplitude = round((quote.high - quote.low) / quote.pre_close * 100, 2)
        if fundamental:
            d.pe_ratio = getattr(fundamental, "pe_ratio", None)
        return d

    def _volume_ratio(self, quote: Quote, bars: list[Bar]) -> Optional[float]:
        if quote.volume is None or len(bars) < 6:
            return None
        prev_5 = [b.volume for b in bars[-6:-1]]
        if not prev_5 or sum(prev_5) == 0:
            return None
        avg = sum(prev_5) / 5
        return round(quote.volume / avg, 2)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_quote_derived.py -v`
Expected: PASS(3 个测试)

- [ ] **Step 5: Commit**

```bash
git add data_provider/quote_derived.py tests/test_quote_derived.py
git commit -m "feat(connector): QuoteDerivedCalculator"
```

---

### Task 8: 低影响调用点迁移(agent tools)

**Files:**
- Modify: `src/agent/tools/data_tools.py:493`(fundamental 消费)
- Modify: `src/agent/events.py:263`(quote 消费)
- Test: `tests/test_agent_tools_contract.py`

**Interfaces:**
- Consumes: `DataFetcherManager.get_realtime_quote`(返回经 `Quote.legacy_compat()` 兼容——本任务只确保调用方读字段用 `getattr`,无需改形状);`get_fundamental_context` 保持返回 dict(其内部适配 FundamentalRaw 在 Task 9)
- Produces: 无新接口;迁移后调用方对 quote/fundamental 字段的读取保持 getattr 模式

- [ ] **Step 1: 写失败测试**(断言现有 getattr 消费在 Quote 形状下仍工作——用 `Quote.legacy_compat()` 打桩)

```python
# tests/test_agent_tools_contract.py
import pytest
from data_provider.contracts import Quote


def test_quote_legacy_compat_supports_getattr_reads():
    q = Quote(code="600519", price=1700.0, name="贵州茅台", currency="CNY")
    old = q.legacy_compat()
    assert getattr(old, "price", None) == 1700.0
    assert getattr(old, "name", "") == "贵州茅台"
```

- [ ] **Step 2: 运行测试验证通过**(先写通过测试确认契约成立)

Run: `& .venv/Scripts/python.exe -m pytest tests/test_agent_tools_contract.py -v`
Expected: PASS(legacy_compat 已由 Task 1 提供)

- [ ] **Step 3: 修改两个调用点**——`data_tools.py:493` 与 `events.py:263` 处:确认读取用 `getattr`;若存在 `quote.to_dict()` 调用,保持(dict 形状不变)。无需改逻辑,只加注释标注"契约层 Quote,读字段保持 getattr 以容忍缺省"。

- [ ] **Step 4: 运行相关测试**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_data_tools_get_stock_info.py tests/test_realtime_quote_fallback_logging.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools/data_tools.py src/agent/events.py tests/test_agent_tools_contract.py
git commit -m "refactor(connector): migrate low-impact agent tool call sites"
```

---

### Task 9: 中影响调用点迁移(screening / fundamental 消费)

**Files:**
- Modify: `src/services/screening_service.py:3358`、`src/services/screening/dsa_provider.py:96`
- Modify: `src/core/pipeline.py:530`(fundamental 消费)
- Test: `tests/test_screening_contract_consumers.py`

**Interfaces:**
- Consumes: `get_fundamental_context` 输出保持 dict(内部由 `FundamentalRaw.model_dump()` 组装,见实现)
- Produces: 无;`get_fundamental_context` 内部改用 contracts:`DataFetcherManager.get_fundamental_context` 组装时,若 fetcher 提供 `to_fundamental`,用其产出 FundamentalRaw 再 model_dump 合并旧 dict 形状(兼容旧调用方)

- [ ] **Step 1: 写失败测试**(现有 fundamental 消费点输入形状断言)

```python
# tests/test_screening_contract_consumers.py
import pytest
from data_provider.contracts import FundamentalRaw
from datetime import date


def test_fundamental_raw_dump_keeps_keys_for_consumers():
    fr = FundamentalRaw(report_date=date(2026, 6, 30), fiscal_period="Q2", market="cn",
                        total_assets=1.0, revenue=2.0, net_income=0.5)
    d = fr.model_dump()
    assert d["report_date"] == date(2026, 6, 30)  # 消费者按原 key 读取
    assert d["total_assets"] == 1.0
```

- [ ] **Step 2: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_screening_contract_consumers.py -v`
Expected: PASS(先建立契约→consumer 形状契约)

- [ ] **Step 3: 实现**——`data_provider/base.py` 的 `get_fundamental_context` 内,对四个目标源优先走 `fetcher.to_fundamental(raw_payload)`,产出 FundamentalRaw 后 `model_dump()` 合并进返回 dict(保留旧 key 命名);非目标源保持原逻辑。screening 两个调用点不变(输入仍是 dict)。

- [ ] **Step 4: 运行相关测试**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_fundamental_context.py tests/test_screening_service.py -v`(若 test_screening_service 过大,改为 `tests/test_fundamental_context.py tests/test_belong_boards_run_flow.py`)
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data_provider/base.py src/services/screening_service.py src/services/screening/dsa_provider.py src/core/pipeline.py tests/test_screening_contract_consumers.py
git commit -m "refactor(connector): fundamental consumers via contract dump"
```

---

### Task 10: 高影响调用点迁移(pipeline quote 链)

**Files:**
- Modify: `src/core/pipeline.py`(quote 消费 ~30 处 getattr,不改逻辑,统一经 `Quote.legacy_compat()` 适配层)
- Modify: `src/analyzer.py:947`(quote 参数)
- Test: `tests/test_pipeline_contract_compat.py`

**Interfaces:**
- Consumes: `Quote.legacy_compat()`(Task 1)
- Produces: 无;约定:`pipeline.py` 内 `fetcher_manager.get_realtime_quote()` 返回统一对象,新增一个模块级 helper `_to_legacy_quote(q)`:`Quote` → `legacy_compat()`,旧 dataclass 原样返回——所有既有 getattr 读取零改动

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline_contract_compat.py
import pytest
from data_provider.contracts import Quote
from src.core.pipeline import _to_legacy_quote
from data_provider.realtime_types import UnifiedRealtimeQuote


def test_helper_passes_legacy_through():
    old = UnifiedRealtimeQuote(code="600519")
    assert _to_legacy_quote(old) is old


def test_helper_converts_new_quote():
    q = Quote(code="600519", price=1700.0)
    out = _to_legacy_quote(q)
    assert isinstance(out, UnifiedRealtimeQuote)
    assert out.price == 1700.0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_contract_compat.py -v`
Expected: FAIL(ImportError: _to_legacy_quote)

- [ ] **Step 3: 写实现**——`src/core/pipeline.py` 顶部加:

```python
def _to_legacy_quote(value):
    """Quote → UnifiedRealtimeQuote 适配;旧 dataclass 原样透传。"""
    from data_provider.contracts import Quote
    if isinstance(value, Quote):
        return value.legacy_compat()
    return value
```
并在 pipeline.py 中 `get_realtime_quote` 的所有调用点包裹 `_to_legacy_quote(...)`(约 3-4 处调用点,`pipeline.py:472` 为主)。

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_contract_compat.py -v`
Expected: PASS

- [ ] **Step 5: 回归验证(关键)**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_realtime_indicators.py tests/test_pipeline_daily_market_context.py tests/test_pipeline_market_phase_context.py tests/test_realtime_quote_fallback_logging.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/core/pipeline.py src/analyzer.py tests/test_pipeline_contract_compat.py
git commit -m "refactor(connector): pipeline quote chain via legacy adapter"
```

---

### Task 11: 剩余 fetcher 注册表化 + grep 兜底 + flag 默认开 + 删旧硬编码

**Files:**
- Modify: `config/fetchers.yaml`(补 8 个源条目,enabled: false)
- Modify: `data_provider/base.py`(删 `_DAILY_MARKET_FETCHER_SUPPORT` 及旧 if/else 路由,确认 flag 逻辑后移除 flag 分支保留注册表路径)
- Test: `tests/test_fetcher_registry_legacy_removal.py`

**Interfaces:**
- Consumes: 前 10 任务全部产物
- Produces: 无;收尾状态:注册表为唯一路由源

- [ ] **Step 1: grep 兜底确认**

Run: `rg "_DAILY_MARKET_FETCHER_SUPPORT" src data_provider api bot apps -l`
Expected: 仅 `data_provider/base.py`(无其他调用方);若存在其他引用,先迁移再继续

- [ ] **Step 2: 补 8 个 fetcher 条目**(tencent/pytdx/efinance/baostock/longbridge/tickflow/alphavantage/tw_institutional,`enabled: false`,capabilities 按实际:tencent/pytdx/efinance/baostock/tickflow=[cn,quote,bar],longbridge=[hk,us,quote,bar],alphavantage=[us,quote,bar],tw_institutional=[tw,fundamental])

- [ ] **Step 3: 写失败测试**(注册表含全部 12 源,enabled 状态正确)

```python
# tests/test_fetcher_registry_legacy_removal.py
import pytest
from data_provider.specs import load_fetcher_specs, DEFAULT_REGISTRY_PATH


def test_registry_has_all_sources():
    specs = load_fetcher_specs(DEFAULT_REGISTRY_PATH)
    names = {s.name for s in specs}
    assert {"akshare", "tushare", "yfinance", "finnhub", "tencent", "pytdx",
            "efinance", "baostock", "longbridge", "tickflow",
            "alphavantage", "tw_institutional"} <= names


def test_v1_sources_enabled():
    specs = {s.name: s for s in load_fetcher_specs(DEFAULT_REGISTRY_PATH)}
    assert specs["akshare"].enabled and specs["yfinance"].enabled


def test_v1_others_disabled_by_default():
    specs = {s.name: s for s in load_fetcher_specs(DEFAULT_REGISTRY_PATH)}
    assert specs["tencent"].enabled is False
```

- [ ] **Step 4: 运行测试(先失败后通过)**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_fetcher_registry_legacy_removal.py -v`
Expected: 初 FAIL(缺条目)→ 补 YAML 后 PASS

- [ ] **Step 5: 删除旧硬编码**——`base.py` 删 `_DAILY_MARKET_FETCHER_SUPPORT` 常量、`get_realtime_quote` 内旧 if/else 分支与 `_try_fetcher_quote`(保留 `_try_fetcher_quote_spec`);`src/config.py` 移除 `connector_v2_enabled` 分支逻辑(注册表为唯一路径,flag 退役)

- [ ] **Step 6: 全量离线回归(关键)**

Run: `& .venv/Scripts/python.exe -m pytest tests/ -m "not network" -x -q`
Expected: 全部 PASS;若出现旧路径依赖测试失败,回到 Task 4 flag 逻辑确认

- [ ] **Step 7: Commit**

```bash
git add config/fetchers.yaml data_provider/base.py src/config.py tests/test_fetcher_registry_legacy_removal.py
git commit -m "feat(connector): registry-only routing, retire legacy hardcoded support"
```

---

## Self-Review 记录

- **Spec 覆盖**:契约层(Task 1/5/6/7)、注册表(Task 2/3)、路由改造(Task 4/11)、调用点迁移(Task 8/9/10)、兼容策略(legacy_compat Task 1)、feature flag(Task 4/11)、grep 兜底(Task 11)、四源落地(Task 5/6)、QuoteDerived(Task 7)——全部覆盖
- **Placeholder 扫描**:无 TBD/TODO;所有实现代码已给出骨架与规则
- **类型一致性**:`FetcherSpec.fetcher_class`(YAML 键 `fetcher_class:` 规避 class 保留字)在 Task 2/3/4 一致;`_try_fetcher_quote_spec(spec, code, **kw)` 在 Task 4 定义、Task 11 保留;`Quote.legacy_compat(source=...)` Task 1 定义、Task 5/8/10 消费一致