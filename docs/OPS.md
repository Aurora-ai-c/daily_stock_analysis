# Operations Guide (OPS)

运维与排障手册。面向"每日任务已启用"后的日常运行。

## 1. 每日任务

- 工作流：`.github/workflows/00-daily-analysis.yml`（默认暂停，需用户在 GitHub UI 手动启用）。
- 触发前务必核对：仓库 `Settings -> Secrets` 中的 `TUSHARE_TOKEN`、`GITHUB_PAT`、`DEEPSEEK_API_KEY` 等是否齐全有效；受限网络需配置 `github_proxy` / `github_ca_bundle`。
- 客户端"状态"Tab 显示 `last_success_ts`；超过 48h 未成功即亮红灯（`STALE_THRESHOLD_SECONDS`）。

## 2. 数据源健康（P0 时效风险）

AlphaEvo 信号依赖 OHLCV 输入。若**所有**行情源熔断（`all_failed`），信号将直接为空。

- 熔断阈值已改为**环境变量可配**（运维可调灵敏度，避免瞬时抖动误熔断）：
  - `REALTIME_CB_FAILURE_THRESHOLD` / `REALTIME_CB_COOLDOWN_SECONDS`（实时/筹码）
  - `CHIP_CB_FAILURE_THRESHOLD` / `CHIP_CB_COOLDOWN_SECONDS`
- 诊断命令（离线，无网络）：
  ```bash
  python -m data_provider.self_test          # 打印配置 + 熔断状态
  python -m data_provider.self_test --probe  # 实时探测各源（带超时）
  ```
- 最近一次运行的数据源健康会写入 `run_state.json` 的 `data_source_health`，客户端"状态 -> 数据源健康"展示；若 `all_failed` 弹红色横幅。

## 3. 成本护栏（预算可见性 + 告警）

- 客户端"设置"可设 `budget_daily_usd` 与 `budget_mode`（`warn` / `block`）。
- `warn` 模式：超额**不中止**，仅在客户端"今日花费"卡片显示红色横幅，并写 `run_state.budget_over=true`。
- `block` 模式：触发前预估超预算则拒绝触发（`error: budget_exceeded`）。
- 花费为乐观累加（每次触发 `estimate_cost`），非精确账单。`/api/status` 返回 `today_spend_usd / budget_daily_usd / budget_over / budget_usage_ratio`。

## 4. 非日线周期

`stock_service.get_history_data` 现已支持 `period=daily|weekly|monthly`。底层仍抓取日线后按 `W`（周）/ `ME`（月）重采样，调用点传入 `days` 会按周期放大（weekly×5、monthly×22）。

## 5. 本地测试分层

- 离线门禁：`pytest -m "not network" -c tests/pytest.ini`（CI `scripts/ci_gate.sh offline-tests`）。
- 本地默认跳过网络用例：`tests/conftest.py` 在 `DSA_RUN_ONLINE!=1` 时跳过 `network` 标记用例。
- 在线门禁（仅 schedule / workflow_dispatch，带 Secrets）：`ci.yml` 的 `online-tests` 作业跑 `pytest -m network`。
- 标记新网络用例：`@pytest.mark.network`。
