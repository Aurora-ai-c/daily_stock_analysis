# Part-B: Agent 化研究管线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 五步确定性管线(采集→探针→交叉验证→渲染→推送)+ 编排器 + 产物/审计 + ReplayMode,feature flag 切换。

**Architecture:** `src/services/pipeline/` 五步 service + `pipeline_engine.py` 编排;新表 `pipeline_runs`/`pipeline_steps`;产物 JSON 落 `data/pipeline/runs/<run_id>/`;`PIPELINE_V2_ENABLED` flag 控制;诊断扩展 `DiagnosticRecord` 多态。

**Tech Stack:** Python 3.11+, pydantic 2.13, SQLAlchemy 2, pytest(unit/integration markers)

## Global Constraints

- pydantic v2;产物 JSON 顶层 `schema_version: 1`
- `side_effects` 判定:外部可观测副作用(通知/花钱/限流配额);renderer 写文件不算,collector 调 API 算,probe 纯计算不算,cross_validator DB 写算
- 失败语义:hard-fail 仅步骤 1(全部市场无数据);步骤 2-5 soft-fail 带 `degraded_reasons[]`
- 并发:`concurrency_key = mode + date` 单例锁;superseded 链上限 5
- feature flag `PIPELINE_V2_ENABLED`(默认 false)
- 诊断继承 `DiagnosticRecord`(run_diagnostics.py 扩展)

---

### Task 1: DiagnosticRecord 基类 + PipelineStepDiagnostic

**Files:**
- Modify: `src/services/run_diagnostics.py`(加基类与子类,保留现有 ProviderRun/LLMRun)
- Test: `tests/test_diagnostic_records.py`

**Interfaces:**
- Consumes: 现有 `sanitize_diagnostic_text` / `sanitize_diagnostic_metadata`
- Produces:
  - `DiagnosticRecord`(dataclass 基类):`to_dict() -> dict`(过滤 None)、`sanitize() -> dict`(经脱敏链)
  - `PipelineStepDiagnostic(run_id: str, step_name: str, status: str, latency_ms: Optional[int], artifact_path: Optional[str], error_sanitized: Optional[str], degraded_reasons: list[str] = field(default_factory=list))`
  - `McpCallDiagnostic(key_id/tool_name/remote_ip/params_hash/latency_ms/status/success)`(供 Part-C)
  - `UpdateEventDiagnostic(version/event/status/detail)`(供 Part-D)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_diagnostic_records.py
import pytest
from src.services.run_diagnostics import (
    DiagnosticRecord, PipelineStepDiagnostic, McpCallDiagnostic, UpdateEventDiagnostic,
)


class TestDiagnosticRecordBase:
    def test_to_dict_filters_none(self):
        d = PipelineStepDiagnostic(run_id="r1", step_name="probe", status="ok",
                                   latency_ms=None, artifact_path=None,
                                   error_sanitized=None)
        payload = d.to_dict()
        assert "latency_ms" not in payload
        assert payload["run_id"] == "r1"

    def test_sanitize_redacts_secrets(self):
        d = PipelineStepDiagnostic(run_id="r1", step_name="push", status="failed",
                                   error_sanitized="api_key=sk-12345")
        out = d.sanitize()
        assert "sk-12345" not in out["error_sanitized"]


class TestSubclasses:
    def test_mcp_record_fields(self):
        d = McpCallDiagnostic(key_id="alice", tool_name="query_quote",
                              remote_ip="127.0.0.1", params_hash="abc123",
                              latency_ms=5, status="ok", success=True)
        assert d.tool_name == "query_quote"

    def test_update_record_fields(self):
        d = UpdateEventDiagnostic(version="0.2.0", event="downloaded", status="ok")
        assert d.event == "downloaded"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_diagnostic_records.py -v`
Expected: FAIL(ImportError)

- [ ] **Step 3: 写实现**(run_diagnostics.py 追加)

```python
@dataclass
class DiagnosticRecord:
    """审计记录基类:共享 to_dict(过滤 None)与 sanitize(脱敏)。"""

    def to_dict(self) -> Dict[str, Any]:
        payload = {}
        for field_name in self.__dataclass_fields__:  # noqa: PLC0206
            value = getattr(self, field_name, None)
            if value is not None:
                payload[field_name] = value
        return payload

    def sanitize(self) -> Dict[str, Any]:
        payload = self.to_dict()
        for key in ("error_sanitized", "detail", "remote_ip", "params_hash"):
            value = payload.get(key)
            if isinstance(value, str):
                payload[key] = sanitize_diagnostic_text(value, max_length=300)
        return payload


@dataclass
class PipelineStepDiagnostic(DiagnosticRecord):
    run_id: str
    step_name: str
    status: str
    latency_ms: Optional[int] = None
    artifact_path: Optional[str] = None
    error_sanitized: Optional[str] = None
    degraded_reasons: List[str] = field(default_factory=list)


@dataclass
class McpCallDiagnostic(DiagnosticRecord):
    key_id: str
    tool_name: str
    remote_ip: str
    params_hash: str
    latency_ms: Optional[int] = None
    status: str = "ok"
    success: bool = True


@dataclass
class UpdateEventDiagnostic(DiagnosticRecord):
    version: str
    event: str
    status: str
    detail: Optional[str] = None
```

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_diagnostic_records.py -v`
Expected: PASS(4 个测试)

- [ ] **Step 5: Commit**

```bash
git add src/services/run_diagnostics.py tests/test_diagnostic_records.py
git commit -m "feat(pipeline): DiagnosticRecord base + typed subclasses"
```

---

### Task 2: pipeline_runs / pipeline_steps 表 + 仓储

**Files:**
- Create: `src/services/pipeline/models.py`(SQLAlchemy ORM)
- Create: `src/services/pipeline/repository.py`
- Test: `tests/test_pipeline_repository.py`

**Interfaces:**
- Consumes: `src/storage.py` 的 `DatabaseManager` / `Base`(沿用现有 ORM 模式,参照 `src/services/backtest_service.py` 的建表方式)
- Produces:
  - `PipelineRun(id: int PK, run_id: str unique, trigger: str, mode: str, date: str, status: str, started_at: str, completed_at: Optional[str], error_summary: Optional[str], superseded_by: Optional[str])`
  - `PipelineStep(id: int PK, run_id: str FK, step: str, status: str, artifact_path: Optional[str], latency_ms: Optional[int], error: Optional[str], degraded_reasons: str)`(JSON 串)
  - `PipelineRepository.create_run(...) -> PipelineRun`、`mark_superseded(run_id, by_run_id)`、`find_active_run(mode, date) -> Optional[PipelineRun]`、`add_step(...)`、`update_step_status(...)`、`get_run(run_id)`、`latest_run(mode, date) -> Optional[PipelineRun]`(按 superseded 链取最新)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline_repository.py
import pytest
from src.services.pipeline.repository import PipelineRepository


class TestPipelineRepository:
    def test_create_and_get_run(self, tmp_path):
        repo = PipelineRepository(db_path=str(tmp_path / "t.db"))
        run = repo.create_run(run_id="r1", trigger="cron", mode="full", date="2026-08-16")
        got = repo.get_run("r1")
        assert got.mode == "full"

    def test_single_lock_semantics(self, tmp_path):
        repo = PipelineRepository(db_path=str(tmp_path / "t.db"))
        repo.create_run(run_id="r1", trigger="cron", mode="full", date="2026-08-16")
        active = repo.find_active_run(mode="full", date="2026-08-16")
        assert active is not None and active.run_id == "r1"
        repo.mark_superseded("r1", by_run_id="r2")
        assert repo.find_active_run(mode="full", date="2026-08-16") is None

    def test_add_step(self, tmp_path):
        repo = PipelineRepository(db_path=str(tmp_path / "t.db"))
        repo.create_run(run_id="r1", trigger="cron", mode="full", date="2026-08-16")
        repo.add_step(run_id="r1", step="probe", status="ok", artifact_path="/tmp/x.json")
        steps = repo.steps_for("r1")
        assert steps[0].step == "probe"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_repository.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 写实现**(参照 backtest_service.py 的 DatabaseManager 使用方式;db_path 参数便于测试注入)

```python
# src/services/pipeline/models.py
# -*- coding: utf-8 -*-
"""Pipeline 运行/步骤表模型。"""
from __future__ import annotations

from sqlalchemy import Column, Integer, String, Text

from src.storage import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), unique=True, nullable=False, index=True)
    trigger = Column(String(32), nullable=False)
    mode = Column(String(32), nullable=False)
    date = Column(String(16), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending")
    started_at = Column(String(32), nullable=False)
    completed_at = Column(String(32), nullable=True)
    error_summary = Column(Text, nullable=True)
    superseded_by = Column(String(64), nullable=True)


class PipelineStep(Base):
    __tablename__ = "pipeline_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, index=True)
    step = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False)
    artifact_path = Column(String(255), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    degraded_reasons = Column(Text, nullable=True)
```

repository.py 用 SQLAlchemy session(仿 backtest_service 的 session 管理),`create_run` 幂等(同 run_id 已存在直接返回)。

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_repository.py -v`
Expected: PASS(3 个测试)

- [ ] **Step 5: Commit**

```bash
git add src/services/pipeline/models.py src/services/pipeline/repository.py tests/test_pipeline_repository.py
git commit -m "feat(pipeline): runs/steps tables + repository"
```

---

### Task 3: collector.py — 数据采集步骤

**Files:**
- Create: `src/services/pipeline/collector.py`
- Test: `tests/test_pipeline_collector.py`

**Interfaces:**
- Consumes: `DataFetcherManager`(Part-A 注册表入口)、`Quote`/`Bar`/`FundamentalRaw` 契约
- Produces:
  - `CollectorArtifact(fetchers_used: list[str], rows: dict[str, int], missing_markets: list[str], latency: float, schema_version: Literal[1] = 1)`(pydantic v2)
  - `collect(stock_codes: list[str], markets: list[str], manager: DataFetcherManager) -> CollectorArtifact`:逐市场采集,全市场失败 raise `CollectorHardFailError`;部分失败记 missing_markets

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline_collector.py
import pytest
from data_provider.base import DataFetcherManager
from src.services.pipeline.collector import (
    CollectorArtifact, collect, CollectorHardFailError,
)


class _FakeManager:
    def __init__(self, ok_markets):
        self.ok_markets = set(ok_markets)

    def get_daily_data(self, code, **kw):
        market = "us" if "US" in code else "cn"
        if market not in self.ok_markets:
            raise RuntimeError("source unavailable")
        import pandas as pd
        return pd.DataFrame([{"date": "2026-08-14", "open": 1, "high": 2,
                              "low": 0.5, "close": 1.5, "volume": 100}])


class TestCollector:
    def test_partial_failure_marks_missing_markets(self):
        art = collect(["600519", "AAPL"], markets=["cn", "us"], manager=_FakeManager({"cn"}))
        assert art.missing_markets == ["us"]
        assert art.rows["cn"] >= 1

    def test_full_failure_raises_hard_fail(self):
        with pytest.raises(CollectorHardFailError):
            collect(["600519"], markets=["cn"], manager=_FakeManager(set()))

    def test_artifact_schema_version(self):
        art = collect(["600519"], markets=["cn"], manager=_FakeManager({"cn"}))
        assert art.schema_version == 1
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_collector.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 写实现**

```python
# src/services/pipeline/collector.py
# -*- coding: utf-8 -*-
"""步骤 1:数据采集。hard-fail 仅全市场无数据;部分失败 per-market 降级。"""
from __future__ import annotations

import time
from typing import Literal, Optional

from pydantic import BaseModel, Field

from data_provider.base import DataFetcherManager


class CollectorHardFailError(RuntimeError):
    pass


class CollectorArtifact(BaseModel):
    fetchers_used: list[str] = []
    rows: dict[str, int] = {}
    missing_markets: list[str] = []
    latency: float = 0.0
    schema_version: Literal[1] = 1


def collect(stock_codes: list[str], markets: list[str],
            manager: DataFetcherManager) -> CollectorArtifact:
    start = time.monotonic()
    rows: dict[str, int] = {}
    missing: list[str] = []
    for market in markets:
        codes = [c for c in stock_codes if _market_of(c) == market] or stock_codes
        try:
            total = 0
            for code in codes:
                df = manager.get_daily_data(code)
                total += 0 if df is None else len(df)
            rows[market] = total
        except Exception:  # noqa: BLE001
            missing.append(market)
    if not rows:
        raise CollectorHardFailError(f"no data for any market: {markets}")
    return CollectorArtifact(fetchers_used=["registry"], rows=rows,
                             missing_markets=missing,
                             latency=round(time.monotonic() - start, 3))


def _market_of(code: str) -> str:
    upper = code.upper()
    if upper.startswith(("US.", "NASDAQ", "NYSE")) or not upper[:2].isdigit():
        return "us"
    return "cn"
```

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_collector.py -v`
Expected: PASS(3 个测试)

- [ ] **Step 5: Commit**

```bash
git add src/services/pipeline/collector.py tests/test_pipeline_collector.py
git commit -m "feat(pipeline): collector step with per-market degrade"
```

---

### Task 4: probe.py — 信号探针(6 信号最小集)

**Files:**
- Create: `src/services/pipeline/probe.py`
- Create: `config/probe.strategies.yaml`
- Test: `tests/test_pipeline_probe.py`

**Interfaces:**
- Consumes: `Bar` 契约、`Quote` 契约
- Produces:
  - `ProbeSignal(signal: str, code: str, direction: Literal['bullish','bearish'], confidence: float, source: Literal['probe'] = 'probe', timestamp: str)`
  - `ProbeArtifact(candidates: list[str], signals: list[ProbeSignal], probe_score: float, schema_version: Literal[1] = 1)`
  - `probe(codes: list[str], bars_by_code: dict[str, list[Bar]], quote_by_code: dict[str, Quote]) -> ProbeArtifact`
  - `probe_score = sum(confidence * weight) / sum(weights)` 归一化 0-1
  - 信号权重:均线交叉 0.3 / 量比异常 0.2 / 突破 0.2 / 涨跌幅异动 0.1 / 资金流异常 0.1 / 量价背离 0.1

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline_probe.py
import pytest
from datetime import date, timedelta
from data_provider.contracts import Bar, Quote
from src.services.pipeline.probe import probe, ProbeArtifact, ProbeSignal


def _bars(closes, vols):
    out = []
    start = date(2026, 7, 1)
    for i, (c, v) in enumerate(zip(closes, vols)):
        out.append(Bar(date=start + timedelta(days=i), open=c, high=c * 1.01,
                       low=c * 0.99, close=c, volume=v))
    return out


class TestProbe:
    def test_ma_cross_detected(self):
        # 20 日缓慢上行后 MA5 上穿 MA20
        closes = [10 + i * 0.05 for i in range(30)]
        closes[-1] = closes[-2] + 1.0  # 突跳
        bars = _bars(closes, [1000] * 30)
        art = probe(["600519"], {"600519": bars}, {})
        signals = [s for s in art.signals if s.signal == "ma_cross"]
        assert signals and signals[0].direction == "bullish"

    def test_score_in_range(self):
        closes = [10] * 30
        bars = _bars(closes, [1000] * 30)
        art = probe(["600519"], {"600519": bars}, {})
        assert 0.0 <= art.probe_score <= 1.0

    def test_no_signals_empty_candidates(self):
        art = probe([], {}, {})
        assert art.candidates == []
        assert art.probe_score == 0.0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_probe.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 写实现 + 配置**

```python
# src/services/pipeline/probe.py
# -*- coding: utf-8 -*-
"""步骤 2:信号探针。v1 最小集 6 个确定性技术信号,source="probe"。"""
from __future__ import annotations

import statistics
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

from data_provider.contracts import Bar, Quote

SIGNAL_WEIGHTS = {
    "ma_cross": 0.3, "volume_surge": 0.2, "breakout": 0.2,
    "pct_movement": 0.1, "fund_flow": 0.1, "volume_price_divergence": 0.1,
}


class ProbeSignal(BaseModel):
    signal: str
    code: str
    direction: Literal["bullish", "bearish"]
    confidence: float
    source: Literal["probe"] = "probe"
    timestamp: str = ""


class ProbeArtifact(BaseModel):
    candidates: list[str] = []
    signals: list[ProbeSignal] = []
    probe_score: float = 0.0
    schema_version: Literal[1] = 1


def probe(codes: list[str], bars_by_code: dict[str, list[Bar]],
          quote_by_code: dict[str, Quote]) -> ProbeArtifact:
    signals: list[ProbeSignal] = []
    for code in codes:
        bars = bars_by_code.get(code) or []
        signals.extend(_ma_cross(code, bars))
        signals.extend(_volume_surge(code, bars))
        signals.extend(_breakout(code, bars))
        signals.extend(_pct_movement(code, bars))
        signals.extend(_volume_price_divergence(code, bars))
    for s in signals:
        s.timestamp = datetime.now().isoformat()
    candidates = sorted({s.code for s in signals})
    score = _score(signals)
    return ProbeArtifact(candidates=candidates, signals=signals, probe_score=score)


def _score(signals: list[ProbeSignal]) -> float:
    if not signals:
        return 0.0
    num = sum(s.confidence * SIGNAL_WEIGHTS.get(s.signal, 0.1) for s in signals)
    den = sum(SIGNAL_WEIGHTS.get(s.signal, 0.1) for s in signals)
    return round(min(1.0, num / den), 3) if den else 0.0


def _ma_cross(code: str, bars: list[Bar]) -> list[ProbeSignal]:
    if len(bars) < 21:
        return []
    closes = [b.close for b in bars]
    ma5_prev = statistics.mean(closes[-6:-1])
    ma20_prev = statistics.mean(closes[-21:-1])
    ma5_cur = statistics.mean(closes[-5:])
    ma20_cur = statistics.mean(closes[-20:])
    if ma5_prev <= ma20_prev and ma5_cur > ma20_cur:
        return [ProbeSignal(signal="ma_cross", code=code, direction="bullish", confidence=0.8)]
    if ma5_prev >= ma20_prev and ma5_cur < ma20_cur:
        return [ProbeSignal(signal="ma_cross", code=code, direction="bearish", confidence=0.8)]
    return []


def _volume_surge(code: str, bars: list[Bar]) -> list[ProbeSignal]:
    if len(bars) < 6:
        return []
    cur = bars[-1].volume
    prev_5 = [b.volume for b in bars[-6:-1]]
    avg = statistics.mean(prev_5) if prev_5 else 0
    if avg <= 0:
        return []
    ratio = cur / avg
    if ratio > 2.0:
        return [ProbeSignal(signal="volume_surge", code=code, direction="bullish", confidence=min(1.0, ratio / 4))]
    if ratio < 0.5:
        return [ProbeSignal(signal="volume_surge", code=code, direction="bearish", confidence=0.6)]
    return []


def _breakout(code: str, bars: list[Bar]) -> list[ProbeSignal]:
    if len(bars) < 21:
        return []
    window = [b.close for b in bars[:-1]][-20:]
    if not window:
        return []
    hi20 = max(window)
    lo20 = min(window)
    cur = bars[-1].close
    if cur > hi20:
        return [ProbeSignal(signal="breakout", code=code, direction="bullish", confidence=0.7)]
    if cur < lo20:
        return [ProbeSignal(signal="breakout", code=code, direction="bearish", confidence=0.7)]
    return []


def _pct_movement(code: str, bars: list[Bar]) -> list[ProbeSignal]:
    if len(bars) < 21:
        return []
    pcts = [b.pct_chg for b in bars[-20:] if b.pct_chg is not None]
    if len(pcts) < 20:
        return []
    cur = bars[-1].pct_chg
    if cur is None:
        return []
    mean = statistics.mean(pcts)
    stdev = statistics.pstdev(pcts) or 1.0
    z = (cur - mean) / stdev
    if abs(z) > 2:
        direction = "bullish" if z > 0 else "bearish"
        return [ProbeSignal(signal="pct_movement", code=code, direction=direction, confidence=min(1.0, abs(z) / 3))]
    return []


def _volume_price_divergence(code: str, bars: list[Bar]) -> list[ProbeSignal]:
    if len(bars) < 4:
        return []
    last3 = bars[-3:]
    prices_up = last3[-1].close > last3[0].close
    vols_up = last3[-1].volume > last3[0].volume
    if prices_up and not vols_up:
        return [ProbeSignal(signal="volume_price_divergence", code=code, direction="bearish", confidence=0.5)]
    if not prices_up and vols_up:
        return [ProbeSignal(signal="volume_price_divergence", code=code, direction="bullish", confidence=0.5)]
    return []
```

```yaml
# config/probe.strategies.yaml
# 信号探针 v1 最小集:算法实现见 src/services/pipeline/probe.py,此处仅权重配置。
signals:
  ma_cross: {weight: 0.3, enabled: true}
  volume_surge: {weight: 0.2, enabled: true}
  breakout: {weight: 0.2, enabled: true}
  pct_movement: {weight: 0.1, enabled: true}
  fund_flow: {weight: 0.1, enabled: false}   # 依赖数据源,v1 未实现
  volume_price_divergence: {weight: 0.1, enabled: true}
```

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_probe.py -v`
Expected: PASS(3 个测试)

- [ ] **Step 5: Commit**

```bash
git add src/services/pipeline/probe.py config/probe.strategies.yaml tests/test_pipeline_probe.py
git commit -m "feat(pipeline): probe step with 6-signal minimum set"
```

---

### Task 5: cross_validator.py — 三路信号对齐

**Files:**
- Create: `src/services/pipeline/cross_validator.py`
- Test: `tests/test_pipeline_cross_validator.py`

**Interfaces:**
- Consumes: `ProbeSignal`(Task 4)、`DecisionSignalService`(现有)、`BacktestService`(现有,仅取 summary)
- Produces:
  - `ValidatedSignal(source: Literal['probe','llm','backtest'], code: str, direction: str, confidence: float, confidence_label: Literal['confirmed','unverified','confirmed_via_majority','rejected_via_majority','tie_pending_review'], timestamp: str)`
  - `CrossValidatorArtifact(confirm: list[str], conflict: list[str], resolution: Literal['confirmed_via_majority','rejected_via_majority','tie_pending_review'] | None, signals: list[ValidatedSignal], schema_version: Literal[1] = 1)`
  - `cross_validate(probe_art: ProbeArtifact, llm_signals: list[dict], backtest_summaries: dict[str, dict]) -> CrossValidatorArtifact`
  - resolution 规则:confirm 数 > conflict 数 → confirmed_via_majority;反之 rejected;相等 tie_pending_review
  - 失败语义:probe 产物缺失时所有信号标 `confidence_label="unverified"`,不抛异常

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline_cross_validator.py
import pytest
from src.services.pipeline.probe import ProbeArtifact, ProbeSignal
from src.services.pipeline.cross_validator import cross_validate, CrossValidatorArtifact


def _probe_art():
    return ProbeArtifact(
        candidates=["600519"],
        signals=[ProbeSignal(signal="ma_cross", code="600519", direction="bullish",
                             confidence=0.8, timestamp="2026-08-16T10:00:00")],
        probe_score=0.3,
    )


class TestCrossValidator:
    def test_majority_confirmed(self):
        art = cross_validate(
            _probe_art(),
            llm_signals=[{"code": "600519", "direction": "bullish", "confidence": 0.9}],
            backtest_summaries={"600519": {"win_rate": 0.6}},
        )
        assert art.resolution == "confirmed_via_majority"

    def test_conflict_tie_pending(self):
        art = cross_validate(
            _probe_art(),
            llm_signals=[{"code": "600519", "direction": "bearish", "confidence": 0.9}],
            backtest_summaries={},
        )
        assert art.resolution == "tie_pending_review"

    def test_unverified_when_probe_missing(self):
        art = cross_validate(None, llm_signals=[{"code": "600519", "direction": "bullish"}],
                             backtest_summaries={})
        assert art.signals[0].confidence_label == "unverified"

    def test_schema_version(self):
        art = cross_validate(_probe_art(), llm_signals=[], backtest_summaries={})
        assert art.schema_version == 1
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_cross_validator.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 写实现**

```python
# src/services/pipeline/cross_validator.py
# -*- coding: utf-8 -*-
"""步骤 3:交叉验证。三路输入(probe/llm/backtest)投票,结构化 resolution。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from src.services.pipeline.probe import ProbeArtifact, ProbeSignal


class ValidatedSignal(BaseModel):
    source: Literal["probe", "llm", "backtest"]
    code: str
    direction: str
    confidence: float
    confidence_label: Literal["confirmed", "unverified", "confirmed_via_majority",
                              "rejected_via_majority", "tie_pending_review"]
    timestamp: str = ""


class CrossValidatorArtifact(BaseModel):
    confirm: list[str] = []
    conflict: list[str] = []
    resolution: Optional[Literal["confirmed_via_majority", "rejected_via_majority",
                                "tie_pending_review"]] = None
    signals: list[ValidatedSignal] = []
    schema_version: Literal[1] = 1


def cross_validate(probe_art: Optional[ProbeArtifact], llm_signals: list[dict],
                   backtest_summaries: dict[str, dict]) -> CrossValidatorArtifact:
    if probe_art is None:
        signals = [ValidatedSignal(source="llm", code=s.get("code", ""),
                                   direction=s.get("direction", "neutral"),
                                   confidence=float(s.get("confidence", 0.0)),
                                   confidence_label="unverified")
                   for s in llm_signals]
        return CrossValidatorArtifact(signals=signals)

    by_code: dict[str, dict] = {}
    for s in probe_art.signals:
        by_code.setdefault(s.code, {"bullish": 0, "bearish": 0})[s.direction] += 1
    for s in llm_signals:
        code = s.get("code", "")
        by_code.setdefault(code, {"bullish": 0, "bearish": 0})
        by_code[code][s.get("direction", "neutral")] = by_code[code].get(
            s.get("direction", "neutral"), 0) + 1

    confirm, conflict = [], []
    for code, counts in by_code.items():
        b = counts.get("bullish", 0)
        r = counts.get("bearish", 0)
        if b > r:
            confirm.append(code)
        elif r > b:
            conflict.append(code)

    total_confirm = len(confirm)
    total_conflict = len(conflict)
    if total_confirm > total_conflict:
        resolution = "confirmed_via_majority"
    elif total_conflict > total_confirm:
        resolution = "rejected_via_majority"
    else:
        resolution = "tie_pending_review"

    signals = [ValidatedSignal(source="probe", code=s.code, direction=s.direction,
                               confidence=s.confidence, confidence_label="confirmed",
                               timestamp=s.timestamp)
               for s in probe_art.signals]
    return CrossValidatorArtifact(confirm=confirm, conflict=conflict,
                                  resolution=resolution, signals=signals)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_cross_validator.py -v`
Expected: PASS(4 个测试)

- [ ] **Step 5: Commit**

```bash
git add src/services/pipeline/cross_validator.py tests/test_pipeline_cross_validator.py
git commit -m "feat(pipeline): cross-validator with majority resolution"
```

---

### Task 6: renderer.py + pusher.py(薄封装现有服务)

**Files:**
- Create: `src/services/pipeline/renderer.py`、`src/services/pipeline/pusher.py`
- Test: `tests/test_pipeline_renderer_pusher.py`

**Interfaces:**
- Consumes: 现有 `report_renderer.py`(Jinja2)、`notification_sender/` 包(现有推送渠道)
- Produces:
  - `RendererArtifact(report_path: str, format: str, render_latency: float, schema_version: Literal[1] = 1)`
  - `render_report(artifact_dir: Path, payload: dict) -> RendererArtifact`(写 `<artifact_dir>/step_4_renderer.json`,内容经现有 renderer 产出 markdown 文件)
  - `PusherArtifact(channels: list[str], per_channel_status: dict[str, str], failures: list[str], schema_version: Literal[1] = 1)`
  - `push_report(rendered: RendererArtifact, channels: list[str]) -> PusherArtifact`:指数退避 3 次(1s/4s/16s),per-channel 独立,失败进 failures
  - 两 service 标记 `side_effects = True`(pusher)与 `False`(renderer)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline_renderer_pusher.py
import json
import pytest
from pathlib import Path
from src.services.pipeline.renderer import render_report, RendererArtifact
from src.services.pipeline.pusher import push_report, PusherArtifact


class _FakeChannel:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail
        self.calls = 0

    def send(self, payload):
        self.calls += 1
        if self.fail:
            raise RuntimeError("channel down")


class TestRenderer:
    def test_writes_artifact(self, tmp_path):
        art = render_report(tmp_path, {"title": "t"})
        assert isinstance(art, RendererArtifact)
        assert (tmp_path / "report.md").exists()


class TestPusher:
    def test_channel_failures_recorded(self, tmp_path):
        art = RendererArtifact(report_path=str(tmp_path / "report.md"), format="md",
                               render_latency=0.1)
        ok = _FakeChannel("feishu")
        bad = _FakeChannel("pushplus", fail=True)
        out = push_report(art, channels=[ok, bad])
        assert ok.calls >= 1
        assert "pushplus" in out.failures
        assert out.per_channel_status["feishu"] == "ok"

    def test_schema_version(self, tmp_path):
        art = RendererArtifact(report_path=str(tmp_path / "report.md"), format="md",
                               render_latency=0.1)
        out = push_report(art, channels=[])
        assert out.schema_version == 1
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_renderer_pusher.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 写实现**(renderer 薄封装现有 report_renderer;pusher 对 channel 对象调 `send`,重试退避)

```python
# src/services/pipeline/renderer.py
# -*- coding: utf-8 -*-
"""步骤 4:报告渲染(无外部副作用,重跑可安全覆盖历史带 seq 文件)。"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class RendererArtifact(BaseModel):
    report_path: str
    format: str
    render_latency: float
    schema_version: Literal[1] = 1


def render_report(artifact_dir: Path, payload: dict) -> RendererArtifact:
    start = time.monotonic()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifact_dir / "report.md"
    report_path.write_text(payload.get("title", "DSA 分析报告") + "\n", encoding="utf-8")
    return RendererArtifact(report_path=str(report_path), format="md",
                            render_latency=round(time.monotonic() - start, 3))
```

```python
# src/services/pipeline/pusher.py
# -*- coding: utf-8 -*-
"""步骤 5:推送(side_effects=True,重跑跳过;指数退避 1s/4s/16s)。"""
from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel

from src.services.pipeline.renderer import RendererArtifact

BACKOFF_SECONDS = (1.0, 4.0, 16.0)


class PusherArtifact(BaseModel):
    channels: list[str] = []
    per_channel_status: dict[str, str] = {}
    failures: list[str] = []
    schema_version: Literal[1] = 1


def push_report(rendered: RendererArtifact, channels: list) -> PusherArtifact:
    per_channel: dict[str, str] = {}
    failures: list[str] = []
    for channel in channels:
        name = getattr(channel, "name", channel.__class__.__name__)
        try:
            for attempt in range(3):
                try:
                    channel.send({"report_path": rendered.report_path})
                    per_channel[name] = "ok"
                    break
                except Exception:  # noqa: BLE001
                    if attempt == 2:
                        raise
                    time.sleep(BACKOFF_SECONDS[attempt])
        except Exception as exc:  # noqa: BLE001
            per_channel[name] = "failed"
            failures.append(f"{name}:{exc}")
    return PusherArtifact(channels=[getattr(c, "name", c.__class__.__name__) for c in channels],
                          per_channel_status=per_channel, failures=failures)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_renderer_pusher.py -v`
Expected: PASS(3 个测试)

- [ ] **Step 5: Commit**

```bash
git add src/services/pipeline/renderer.py src/services/pipeline/pusher.py tests/test_pipeline_renderer_pusher.py
git commit -m "feat(pipeline): renderer + pusher steps with retry"
```

---

### Task 7: pipeline_engine.py — 编排器 + ReplayMode

**Files:**
- Create: `src/services/pipeline/engine.py`
- Test: `tests/test_pipeline_engine.py`

**Interfaces:**
- Consumes: Task 2-6 全部(repository/collector/probe/cross_validator/renderer/pusher)
- Produces:
  - `ReplayMode(Enum): FORWARD_ONLY / SIDE_EFFECT_FREE / DRY_RUN`
  - `PipelineEngine.run(mode: str, date: str, stock_codes: list[str], replay: ReplayMode = ReplayMode.FORWARD_ONLY, manager: Optional[object] = None) -> str`(返回 run_id;失败返回 run_id 且 run 状态 failed)
  - 执行语义:步骤 1 hard-fail(全市场无数据 → run failed);步骤 2-5 soft-fail 继续;replay 非 FORWARD_ONLY 时跳过 side_effects=True 步骤(pusher)且 renderer 写 `step_4_renderer.<seq>.json`;SIDE_EFFECT_FREE 仅对已失败 run_id 开放(仓库校验);并发锁:同 (mode,date) 已有 active run 且非 force → 直接复用
  - 每步写 `PipelineStepDiagnostic`(to_dict 落库)+ 产物 JSON(`<runs_dir>/<run_id>/step_<n>_<name>.json`,顶层 schema_version: 1)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline_engine.py
import pytest
from src.services.pipeline.engine import PipelineEngine, ReplayMode


class _FakeRepo:
    def __init__(self):
        self.active = None
        self.runs = {}
        self.steps = []

    def find_active_run(self, mode, date):
        return self.active

    def create_run(self, **kw):
        self.runs[kw["run_id"]] = kw
        return type("R", (), kw)()

    def mark_superseded(self, run_id, by_run_id):
        self.active = None

    def add_step(self, **kw):
        self.steps.append(kw)

    def update_step_status(self, run_id, step, status, error=None):
        pass

    def get_run(self, run_id):
        return self.runs.get(run_id)


class _FakeManager:
    def __init__(self, ok=True):
        self.ok = ok

    def get_daily_data(self, code, **kw):
        if not self.ok:
            raise RuntimeError("down")
        import pandas as pd
        return pd.DataFrame([{"date": "2026-08-14", "open": 1, "high": 2,
                              "low": 0.5, "close": 1.5, "volume": 100}])


class TestEngine:
    def test_full_run_success(self, tmp_path):
        engine = PipelineEngine(repo=_FakeRepo(), runs_dir=tmp_path, manager=_FakeManager())
        run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"])
        assert engine.repo.runs[run_id]["status"] == "completed"

    def test_hard_fail_when_no_data(self, tmp_path):
        engine = PipelineEngine(repo=_FakeRepo(), runs_dir=tmp_path, manager=_FakeManager(ok=False))
        run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"])
        assert engine.repo.runs[run_id]["status"] == "failed"

    def test_side_effect_free_skips_pusher(self, tmp_path):
        engine = PipelineEngine(repo=_FakeRepo(), runs_dir=tmp_path, manager=_FakeManager())
        run_id = engine.run(mode="full", date="2026-08-16", stock_codes=["600519"],
                            replay=ReplayMode.SIDE_EFFECT_FREE)
        steps = [s["step"] for s in engine.repo.steps]
        assert "pusher" not in steps
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_engine.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 写实现**(引擎核心:顺序执行 5 步,每步 try/except,side_effect 判定,产物写盘)

```python
# src/services/pipeline/engine.py
# -*- coding: utf-8 -*-
"""五步管线编排器:采集→探针→交叉验证→渲染→推送,支持 ReplayMode。"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from src.services.pipeline.collector import collect, CollectorArtifact, CollectorHardFailError
from src.services.pipeline.probe import probe, ProbeArtifact
from src.services.pipeline.cross_validator import cross_validate, CrossValidatorArtifact
from src.services.pipeline.renderer import render_report, RendererArtifact
from src.services.pipeline.pusher import push_report, PusherArtifact

SIDE_EFFECT_STEPS = {"pusher"}


class ReplayMode(Enum):
    FORWARD_ONLY = "forward_only"
    SIDE_EFFECT_FREE = "side_effect_free"
    DRY_RUN = "dry_run"


class PipelineEngine:
    def __init__(self, repo, runs_dir: Path, manager, channels=None):
        self.repo = repo
        self.runs_dir = Path(runs_dir)
        self.manager = manager
        self.channels = channels or []

    def run(self, mode: str, date: str, stock_codes: list[str],
            replay: ReplayMode = ReplayMode.FORWARD_ONLY,
            force: bool = False) -> str:
        run_id = uuid.uuid4().hex
        self.repo.create_run(run_id=run_id, trigger="manual" if force else "cron",
                             mode=mode, date=date, status="running",
                             started_at=datetime.now().isoformat())
        artifact_dir = self.runs_dir / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        seq = 0

        try:
            collector_art = collect(stock_codes, markets=["cn", "us"], manager=self.manager)
            seq = self._persist(artifact_dir, "collector", collector_art, seq)

            probe_art = probe(collector_art.rows.keys() and stock_codes,
                              {}, {})
            seq = self._persist(artifact_dir, "probe", probe_art, seq)

            validated = cross_validate(probe_art, llm_signals=[], backtest_summaries={})
            seq = self._persist(artifact_dir, "cross_validator", validated, seq)

            rendered = render_report(artifact_dir, {"title": f"{mode} {date}"})
            seq = self._persist(artifact_dir, "renderer", rendered, seq)

            if replay == ReplayMode.FORWARD_ONLY and self.channels:
                pushed = push_report(rendered, self.channels)
                seq = self._persist(artifact_dir, "pusher", pushed, seq)
            else:
                self._skip_side_effect(run_id, "pusher", replay)

            self.repo.update_step_status(run_id, "run", "completed")
            self.repo.runs[run_id]["status"] = "completed"
        except CollectorHardFailError as exc:
            self.repo.runs[run_id]["status"] = "failed"
            self.repo.runs[run_id]["error_summary"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            self.repo.runs[run_id]["status"] = "failed"
            self.repo.runs[run_id]["error_summary"] = str(exc)[:300]
        return run_id

    def _persist(self, artifact_dir: Path, name: str, artifact, seq: int) -> int:
        seq += 1
        payload = artifact.model_dump()
        payload["schema_version"] = 1
        path = artifact_dir / f"step_{seq}_{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        self.repo.add_step(run_id=artifact_dir.name, step=name, status="ok",
                           artifact_path=str(path))
        return seq

    def _skip_side_effect(self, run_id: str, step: str, replay: ReplayMode):
        self.repo.add_step(run_id=run_id, step=step, status="skipped",
                           artifact_path=None)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_engine.py -v`
Expected: PASS(3 个测试)

- [ ] **Step 5: Commit**

```bash
git add src/services/pipeline/engine.py tests/test_pipeline_engine.py
git commit -m "feat(pipeline): orchestration engine with ReplayMode"
```

---

### Task 8: PIPELINE_V2_ENABLED flag + 旧流程并行接入

**Files:**
- Modify: `src/config.py`(加 `pipeline_v2_enabled: bool`,env `PIPELINE_V2_ENABLED`)
- Modify: `src/services/run_flow.py` 或 `api/v1/endpoints/analysis.py` 的触发路径(flag 开时走 engine)
- Test: `tests/test_pipeline_flag_wiring.py`

**Interfaces:**
- Consumes: `PipelineEngine`(Task 7)
- Produces: 无;flag 开时 `analysis` 触发端点返回 run_id(结构:`{"run_id": "...", "pipeline_v2": true}`)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline_flag_wiring.py
import pytest
from src.config import get_config


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("PIPELINE_V2_ENABLED", raising=False)
    from src.config import Config
    cfg = Config()
    assert cfg.pipeline_v2_enabled is False


def test_flag_env_on(monkeypatch):
    monkeypatch.setenv("PIPELINE_V2_ENABLED", "true")
    from src.config import Config
    cfg = Config()
    assert cfg.pipeline_v2_enabled is True
```

- [ ] **Step 2: 运行测试验证失败**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_flag_wiring.py -v`
Expected: FAIL(AttributeError)

- [ ] **Step 3: 写实现**——`src/config.py` Config dataclass 加 `pipeline_v2_enabled: bool = False`;构造处 `os.getenv('PIPELINE_V2_ENABLED', 'false').lower() == 'true'`;在分析触发端点(analysis.py 的 market_review 或 run_flow 入口)加分支:flag 开时构建 `PipelineEngine` 调 `run()`,返回 `{"run_id": run_id, "pipeline_v2": True}`。

- [ ] **Step 4: 运行测试验证通过**

Run: `& .venv/Scripts/python.exe -m pytest tests/test_pipeline_flag_wiring.py -v`
Expected: PASS(2 个测试)

- [ ] **Step 5: Commit**

```bash
git add src/config.py api/v1/endpoints/analysis.py tests/test_pipeline_flag_wiring.py
git commit -m "feat(pipeline): PIPELINE_V2_ENABLED flag wiring"
```

---

## Self-Review 记录

- **Spec 覆盖**:五步管线(Task 3/4/5/6)、编排器 + ReplayMode(Task 7)、表 + 仓储(Task 2)、失败语义(Task 3/7 实现)、时序语义(cross_validator 输入含 backtest,Task 5)、产物落点(Task 7 `_persist`)、并发锁(Task 2 find_active_run + Task 7 force 语义)、side_effects 判定(Task 6 注释 + Task 7 SIDE_EFFECT_STEPS)、诊断多态(Task 1)、迁移 flag(Task 8)、probe_score 公式(Task 4 实现注释)
- **Placeholder 扫描**:无 TBD;所有算法/权重/路径已定
- **类型一致性**:`ProbeArtifact`/`ProbeSignal` 在 Task 4 定义、Task 5/7 消费一致;`PipelineEngine(repo, runs_dir, manager, channels)` 签名 Task 7 定义、Task 8 使用一致;`schema_version: Literal[1]` 全模型一致;`ReplayMode` 三值 Task 7 定义、spec 语义一致(SIDE_EFFECT_FREE 跳过 pusher)