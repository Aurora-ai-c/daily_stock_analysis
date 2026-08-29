# Architecture (after refactor)

记录了 2026-08-27 可行性治理后的代码结构。重点是与治理相关的模块位置。

## 目录重构（巨型文件拆分）

| 原文件 | 新包 | 说明 |
|--------|------|------|
| `data_provider/base.py` (4009 行) | `data_provider/base/` | `exceptions.py` / `_helpers.py`（含 `STANDARD_COLUMNS`、`_resample_to_period` 辅助）/ `fetcher.py`(`BaseFetcher`) / `manager.py`(`DataFetcherManager`) / `__init__.py` |
| `src/analyzer.py` (4810 行) | `src/analyzer/_core.py` + `__init__.py` | 67 个公开符号经 `__all__` 再导出 |
| `src/core/config_registry.py` (5087 行) | `src/core/config_registry/_core.py` + `__init__.py` | |
| `src/services/system_config_service.py` (5557 行) | `src/services/system_config_service/_core.py` + `__init__.py` | |

> 拆分原则：公开 API 通过各包 `__init__.py` 的 `__all__` 再导出，外部 `from X import Y` 不变，避免级联改动。

## 数据流

```
GitHub Actions (00-daily-analysis.yml)
        │  python main.py ...
        ▼
main.py (orchestration)
        │  DataFetcherManager.get_daily_data(stock_code, days, period)
        ▼
data_provider/base/manager.py
   ├─ US 源 (yfinance/tickflow) + CN 源 (efinance/akshare/tushare)
   ├─ 熔断: realtime_types.py (env 阈值)
   └─ period!=daily -> _resample_to_period(W/ME)
        ▼
analyzer -> LLM (deepseek/deepseek-v4-flash 默认) -> report
        ▼
GitHub Artifacts + run_state.json (健康/预算)
```

## 关键模块

- `data_provider/self_test.py`：离线诊断 CLI（配置 + 熔断状态；`--probe` 实时探测）。
- `data_provider/realtime_types.py`：熔断器，阈值由环境变量控制（见 OPS）。
- `src/services/stock_service.py::get_history_data`：支持 weekly/monthly（不再抛 `ValueError`）。
- 统一桌面客户端位于 `apps/client/`（Electron 壳 `apps/client/electron` + Web `apps/client/web`），本地拉起 PyInstaller 冻结后端，SearXNG 本地一键，移动端可经 PWA/隧道远程访问；原 `dsa-cloud-client` 的预算/状态接口能力已并入后端 `api/` 与 `src/services/`。

## 测试分层

- `tests/pytest.ini`：注册 `network` marker。
- `tests/conftest.py`：`DSA_RUN_ONLINE!=1` 时跳过 `network` 用例。
- `scripts/ci_gate.sh offline-tests`：`pytest -m "not network"`，3 路分片。
- `.github/workflows/ci.yml::online-tests`：schedule/dispatch 跑 `pytest -m network`（带 Secrets）。
