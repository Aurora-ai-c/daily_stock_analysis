# DSA 平台升级四项:连接器抽象 / Agent 管线 / 客户端更新 / MCP 集成 — 设计文档

日期:2026-08-16
状态:已批准(经 brainstorming 澄清)
实施顺序:**1 → 2 → 4 → 3**(依赖序)

## 背景与目标

借鉴 Fincept 的四个方向,但按"严谨是流程属性,不是模型属性"的原则收敛为四项基础设施升级:

1. **连接器抽象**:统一 bar/quote/fundamental 三个标准契约 + 注册表 + 配置化启停,消除 12+ fetcher 的耦合差异
2. **Agent 化研究管线**:每日分析升级为五步确定性管线(采集→探针→交叉验证→渲染→推送),每步独立产物 + 可审计日志
3. **客户端更新通道**:dsa-cloud-client exe 增加启动检查 + UI 横幅更新机制
4. **MCP 工具集成**:把系统暴露为标准 MCP server(查询信号/触发分析/读报告),作为外部 AI 工具的遥控协议

四个子系统相对独立,共享"统一数据契约"基础设施,写一个综合 spec 分四章,按子系统拆实施计划。

---

# 第一章:连接器抽象

## 现状(已盘点)

- `data_provider/` 12 个 BaseFetcher(akshare/tushare/yfinance/tencent/pytdx/efinance/baostock/finnhub/alphavantage/longbridge/tickflow)+ tw_institutional + 2 个 fundamental adapter
- `BaseFetcher`(base.py:331):日线列名已统一(date/open/high/low/close/volume/amount/pct_chg),含熔断 CircuitBreaker
- `UnifiedRealtimeQuote`(realtime_types.py:110,dataclass):已含 currency/market/fetched_at/is_stale/fallback_from,缺 bid/ask/tz
- `get_realtime_quote`(base.py:1726):**硬编码 if/else 路由**(is_us/is_hk/is_jp 分支 + `_try_fetcher_quote` 按字符串查)
- `get_fundamental_context`(base.py:3140):已有但无统一形状,akshare/yfinance 两个 adapter 独立
- `_DAILY_MARKET_FETCHER_SUPPORT`(base.py:619):启停硬编码
- 已有范本:`intelligence` 资讯源系统(/api/v1/intelligence,注册表 + 模板 + 启停 + fail-open)

## 设计

### 1. 契约层(data_provider/contracts.py,pydantic v2)

项目 venv 是 pydantic **2.13.4**,契约统一 v2。

| 契约 | 字段 | 规则 |
|---|---|---|
| `Bar` | date/open/high/low/close/volume/amount/pct_chg/turnover_rate | **移除 ma5/ma10/ma20/volume_ratio**(派生指标由调用方计算) |
| `Quote` | 演进现有 `UnifiedRealtimeQuote`:code/name/source/price/change_pct(注释明确"较昨收")/change_amount/volume/amount/volume_ratio/turnover_rate/amplitude/open_price/high/low/pre_close/pe_ratio/pb_ratio/total_mv/circ_mv/change_60d/high_52w/low_52w + 新增 bid/ask/tz + 现有 currency/market/fetched_at/provider_timestamp/is_stale/stale_seconds/fallback_from/data_quality/missing_fields | 完整字段,**缺省容忍**(None 可) |
| `FundamentalRaw` | 三表关键科目 ~25 项(总资产/总负债/营收/净利/经营现金流/投资现金流/筹资现金流/股息率/行业等)+ `report_date: date` + `fiscal_period: Literal['Q1','Q2','Q3','Q4','FY']` + `market` | 分市场口径:A 股 akshare/tushare,美股 yfinance/finnhub |
| `FundamentalDerived` | ROE/PE/PB/股息率(依赖股价,与 raw 分离) | 派生层 |

### 2. 注册表(混合方案:实例数据 + 代码发现)

```
config/fetchers.yaml        # 实例数据: enabled/priority/env_required/rate_limit/timeout
data_provider/specs.py      # FetcherSpec(pydantic v2)
data_provider/registry.py   # discover_fetchers() → list[FetcherSpec]
```

```yaml
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
    health_check: null          # Optional[str],模块路径,运行时可调
    version: "1"
```

`FetcherSpec` 字段:`name/module/class/markets/capabilities: list[Literal['quote','bar','fundamental']]/priority/enabled/rate_limit/timeout/env_required/health_check/version`。

`discover_fetchers()` 流程:
1. 读 config/fetchers.yaml
2. pydantic `FetcherSpec` 校验整体结构(schema 与契约同步,防 YAML typo 漂移)
3. `importlib.import_module(spec.module)` 验证可导入
4. 验证 `spec.class` 确实是 module 里的类(防重命名静默失败)
5. 返回强类型 list

**启动策略**:class 无法导入 = **fail-fast**;env_required 缺失 = **warn-only 降级**(自动禁用该源)。hot-reload v1 不做。

### 3. DataFetcherManager 改造

- 删除硬编码 `_DAILY_MARKET_FETCHER_SUPPORT`(base.py:619)与 `get_realtime_quote` 的 if/else 路由(base.py:1726)
- 改为读注册表按 `capabilities` + `markets` + `priority` 路由
- 熔断器保留;`_try_fetcher_quote` 保留机制,入参改为 spec 驱动
- 无 token 的源启动时自动禁用(降级)

### 4. 调用点切换(彻底重写 + 切换所有调用点)

已盘点风险分级:

| 影响级 | 位置 | 说明 |
|---|---|---|
| 高 | `src/core/pipeline.py`(~30 处 getattr) | 核心分析链,quote 消费 |
| 高 | `src/agent/executor.py`、`src/analyzer.py:947` | agent/分析 quote 消费 |
| 中 | `src/agent/events.py`(alert 规则)、portfolio/risk/technical agent | 工具层 quote 消费 |
| 中 | `src/services/screening_service.py:3358`、`dsa_provider.py:96`、`data_tools.py:493`、`pipeline.py:530` | fundamental 消费 |
| 低 | 各 agent tools | 工具引用 |

迁移顺序:低影响(工具/agent)先行,高影响(核心分析链)后行,每批跑对应测试。

### 5. 落地范围与测试

- v1 契约适配 + 注册表切换:**akshare/tushare/yfinance/finnhub** 四源(覆盖 cn/hk/us 全市场)
- 其余 10 个 fetcher 注册表化但 `enabled: false` 或 mock 占位,逐个补测
- 测试:四源契约适配测试(shape 断言 + 缺省容忍);registry 校验测试(class 不存在 fail-fast、env 缺失 warn-only);路由测试(按 capabilities 过滤);现有回归测试全量保留
- CI 三 shard(耗时平衡)全绿后才删 `_DAILY_MARKET_FETCHER_SUPPORT`

---

# 第二章:Agent 化研究管线

## 现状(已盘点)

- 已有 `src/agent/agents/`(decision/intel/portfolio/risk/technical 五 agent)、`agent/skills/`、`agent/tools/`
- `decision_signal_extractor.py:201`:`extract_and_persist_from_analysis_result(result, ..., trace_id, query_source, report_type, profile_source)` — 输入 **AnalysisResult(LLM 分析后)**,下游消费者
- `backtest_service.py`:信号后验闭环(forward_bars/eval_window_days/胜率统计)
- `run_diagnostics.py`:`ProviderRun`/`LLMRun` dataclass(trace_id/data_type/provider/operation/success/latency_ms/fallback_from/cache_hit/record_count + to_dict 过滤 None)+ 脱敏链(sanitize_diagnostic_text/metadata)
- 云端 `00-daily-analysis.yml`(647 行单 job analyze)+ heartbeat(meta/heartbeat 分支 + worktree push,582-626 行)

## 设计

### 1. 五步管线(src/services/pipeline/ 新目录)

| 步骤 | 服务 | 产物(JSON) | 资产复用 |
|---|---|---|---|
| 1. 数据采集 | `collector.py` | `{fetchers_used, rows, missing_markets[], latency}` | 连接器抽象(第 1 章)统一入口 |
| 2. 信号探针 | `probe.py` | `{candidates, signals[], probe_score}` | **新写**(分析前确定性技术信号扫描,基于 Bar/Quote 契约) |
| 3. 交叉验证 | `cross_validator.py` | `{confirm[], conflict[], resolution}` | `backtest_service` 后验 + `decision_signal_outcome_service` 复查 |
| 4. 报告渲染 | `renderer.py` | `{report_path, format, render_latency}` | 已有 `report_renderer.py`(Jinja2) |
| 5. 推送 | `pusher.py` | `{channels[], per_channel_status, failures[]}` | 已有 `notification_sender/` |

**probe 与 extractor 的关系(关键)**:两者不同语义,不存在 wrapper。
- probe(步骤 2)= 分析**前**的确定性技术信号扫描(新写)
- extractor(现有)= 分析**后**的 LLM 信号提取持久化(decision_signal_extractor.py:201),**移入交叉验证后作为信号来源之一**
- cross_validator 负责对齐两条信号线

### 2. 编排器(pipeline_engine.py)

- 新表 `pipeline_runs`(run_id/trigger/mode/date/status/started_at/completed_at/error_summary/superseded_by)+ `pipeline_steps`(run_id/step/status/artifact_path/latency_ms/error/degraded_reasons)
- 步骤间产物:**结构化 JSON + pydantic v2 schema 校验**,顶层 `schema_version: 1`
- **可回放**:同一 run_id 可重跑任一步

### 3. ReplayMode 协议(副作用控制)

```python
class ReplayMode(Enum):
    FORWARD_ONLY = "forward_only"          # 首跑:全步骤
    SIDE_EFFECT_FREE = "side_effect_free"  # 重跑 1-4,跳过 side_effects=True 步骤
    DRY_RUN = "dry_run"                    # 全跑,push 走 mock
```

- step 配置 `side_effects: bool`,pusher=True
- renderer 产物写 `step_<n>.<seq>.json` 不覆盖(重跑保留历史)
- replay 只对已失败的 run_id 开放(DB 校验)

### 4. 失败语义(细化)

- **hard-fail 仅步骤 1**:全部市场无数据才中止
- 步骤 1 部分失败:per-market 降级,产物 `degraded=True, missing_markets=[...]`,渲染层标"⚠️ 数据缺失"
- 步骤 2-5 soft-fail:产物标注 `degraded_reasons[]`,继续流转
- probe 失败:报告加"⚠️ 信号生成失败,本次仅基础分析"横幅
- cross_validator 失败:信号字段 `confidence="unverified"`,禁止无标注展示

### 5. 时序语义(已确认)

- 当日发信号 → backtest_service 评估未来 1/3/5 日窗口(eval_window_days 支持)
- outcome_service T+1 复查(次日新数据重验)

### 6. 产物落点与云端回写

- 本地(单一源):`data/pipeline/runs/<run_id>/step_<n>_<step_name>.json`
- 云端(镜像只读快照):`meta/runs/<date>/<run_id>.json`,由 workflow 回写
- **独立 `meta/runs` 分支**(与 meta/heartbeat 分开),CI 加 branches-ignore 校验

### 7. 并发治理

- `concurrency_key = mode + date` 单例锁
- 同日多次触发允许:新 run 标记旧 run `superseded`(不删除)
- 与云端 workflow 现有 `concurrency: group: stock-analysis` 一致

### 8. 诊断与审计

- 新增 `StepRun` dataclass(同 ProviderRun 模式,字段:run_id/step_name/status/latency_ms/artifact_path/error_sanitized/degraded_reasons),**扩展 run_diagnostics.py 而非新建模块**
- 沿用脱敏链

### 9. 其他

- **pusher 重试**:指数退避 3 次(1s/4s/16s),per-channel 独立,失败进 failures[]
- **DI 测试**:`step_registry.py` + `StepFactory`,E2E 注入假 collector/probe
- **迁移**:feature flag `PIPELINE_V2_ENABLED`,默认 false;旧流程保留并行,新管线验证后切流

---

# 第三章:客户端更新通道

## 现状(已盘点)

- `apps/dsa-cloud-client/`:PyInstaller **onedir**,`version_info.txt` 0.1.0.0,`app.py:main()` 单进程(`create_app` → `uvicorn.run` 阻塞)
- `dsa_client/github_client.py`:PAT 调 GitHub API(get_runs/dispatch/list_artifacts/download_artifact)
- `dsa_client/config.py`:DPAPI 加密 PAT,`~/.dsa-cloud/`
- `dsa_client/server.py`:token/origin 守卫
- 已有 `desktop-release.yml`(**Electron 版** latest.yml 协议,与 PyInstaller 客户端无关)

## 设计

### 1. 发布流程(新增 .github/workflows/client-release.yml)

- manual dispatch 入参:`release_tag`(semver vX.Y.Z)+ `channel`(默认 stable)+ `platform`(默认 win)+ `arch`(默认 x64)
- 校验 tag 格式 → checkout tag → 构建(参照 desktop-release.yml 的 tag 校验模式)
- `build.ps1` 打包 onedir → zip + sha256 → 生成 updates.json → 创建 GitHub Release(资产:zip + updates.json)
- **dry-run 模式**:仅本地构建 + schema 校验,不上传资产

### 2. updates.json schema(数组式,预留扩展位)

```json
{
  "schema_version": 1,
  "latest_stable": "0.2.0",
  "artifacts": [
    {"platform": "win", "arch": "x64", "channel": "stable",
     "version": "0.2.0", "url": "...", "sha256": "...",
     "release_notes": "...", "published_at": "..."}
  ]
}
```

- 客户端按 `sys.platform` + `platform.machine()` 匹配
- v1 仅 win-x64 stable 产出;schema 已预留多平台/多 channel

### 3. 客户端检查(dsa_client/updater.py 新增)

- **归属**:exe 主进程后台线程(daemon),`main()` 中 uvicorn.run 之前启动,**不经 FastAPI、不经 server 守卫**;线程退出时清 PAT 引用
- 检查源:`github_client.get_latest_release()` 读 release 资产中的 updates.json
- **版本比较**:`packaging.version`(PEP 440),v1 语义:仅 stable,不识别 prerelease,忽略 build metadata
- 结果缓存 24h(`~/.dsa-cloud/update_cache.json`),避免每次启动打 GitHub API
- 网络异常:重试 2 次(指数退避),最终失败静默(仅 log)
- 失败降级:无网/PAT 失效/检查失败 → 静默跳过,不打扰

### 4. 原子替换流程(updater.exe 子进程模式)

```
main.exe 点"重启并更新"
  → spawn updater.exe --apply --version vX.Y.Z(detached)
  → 主进程退出
updater.exe:
  → 等主进程退出(轮询 30s)
  → 备份 ~/.dsa-cloud/app/ → ~/.dsa-cloud/backup/vX.Y.Z_prev/(LRU 保留 3 版)
  → 解压 ~/.dsa-cloud/updates/vX.Y.Z/(先校验 sha256)→ 移动到 ~/.dsa-cloud/app/
  → 启动 main.exe(新)
  → 30s 健康检查(轮询 http://127.0.0.1:{port}/health)
  → 失败:杀掉 → 从 backup 回滚 → 重启旧版 → 写 update.log
```

- 跨进程通信:CLI 参数 + 退出码,不做管道/HTTP
- 替换后首次启动检测 `_version_installed` 写入 config,横幅显示"已更新到 vX.Y.Z"
- 用户自助恢复:`restore.bat`(放 `~/.dsa-cloud/`,文档化)
- updater.exe 自身更新问题:记录风险登记册,v1 不解决

### 5. 版本单源真相

- **dispatch release_tag 为唯一真相源**
- 构建期自动同步:生成 `dsa_client/_version.py`(运行读模块属性)+ 同步 version_info.txt(PE 资源)

### 6. UI 与状态机

- 四面板顶栏横幅"发现新版本 vX.Y.Z" + 下载按钮 + 进度;下载完成后"重启并更新"按钮
- 状态机:`idle → checking → downloading → extracting → ready → restart_pending`(按钮按状态禁用)
- 取消 = 删除已下载,回 idle
- 磁盘预检:下载前检查可用空间 ≥ 1.5× zip 大小
- 解压原子性:临时目录 → 完整性校验 → 原子 rename;失败清理
- update.log:`~/.dsa-cloud/updates/<version>/update.log` 全程记录

### 7. 安全

- sha256 完整性校验(下载后强制,不匹配删除并报错)
- 下载走 GitHub release 资产(https)
- 发布者签名:v1 不做,记录风险登记册
- 检查限流:24h 缓存

### 8. 测试

- updater 单测:semver 比较、数组匹配(platform/arch/channel)、sha256 失败路径、回滚逻辑(mock 子进程)、状态机
- workflow dry-run:本地构建 + schema 校验
- UI 横幅:静态页 JS 单测(有/无更新两种渲染)

---

# 第四章:MCP 工具集成

## 现状(已盘点)

- 项目 venv **无 mcp 模块**(需新增依赖)
- 鉴权:`AuthMiddleware`(api/middlewares/auth.py:37)cookie session,仅挡 `/api/v1/*`
- FastAPI 入口 `api/app.py`;`src/auth.py` 有 `is_auth_enabled`/`verify_session`
- 并发已有:分析 endpoint 有 `_try_acquire_market_review_lock`

## 设计

### 1. 部署形态

- 官方 `mcp` SDK(streamable HTTP),**内嵌现有 FastAPI**(`app.mount("/mcp", ...)`,api/app.py),零新进程
- v1 默认仅 127.0.0.1;远程部署文档化(前置 nginx 限流)
- 与 `/api/v1/*` 中间件边界:AuthMiddleware 只挡 /api/v1/*,MCP 路径自带 key 校验;OpenAPI 文档标注"/mcp/* 独立鉴权"
- 生命周期:mcp 无独立 lifespan,随 FastAPI lifespan 启停

### 2. 依赖治理

- pin `mcp==1.2.x`(锁 minor,不用 >=1.x)
- CI 加 MCP 工具 happy-path 冒烟(每次 PR 跑 8 工具)
- 季度升级评审窗口

### 3. 鉴权(多 key + scope + key_id 审计)

```yaml
# 环境变量
MCP_API_KEYS: '{"key_alice":"<sha256>","key_bob":"<sha256>"}'   # key_id → key 哈希
MCP_KEY_key_alice_SCOPE: "read:basic,read:sensitive"
MCP_KEY_key_bob_SCOPE: "read:basic,read:sensitive,write:trigger,read:status"
```

- 存储 **sha256 哈希**非明文;每 key 独立审计标签(key_id)
- 未配置 `MCP_API_KEYS` → MCP 端点直接 404(默认关闭)
- 轮换流程文档化:加新 key → 客户端切换 → 撤旧 key

### 4. 工具集(v1 共 8 个,required_scope 每工具独立)

| 工具 | 输入 | 输出 | scope | 底层服务 |
|---|---|---|---|---|
| `query_quote` | code | Quote 契约 | read:basic | DataFetcherManager.get_realtime_quote(第 1 章产物) |
| `query_fundamental` | code | Fundamental 契约 | read:sensitive | 第 1 章产物 |
| `query_signal` | code/date/limit | decision_signal + 后验命中 | read:sensitive | DecisionSignalService + backtest_service |
| `read_report` | date/type/market | 报告 markdown + 元数据 | read:sensitive | report_renderer / history_service |
| `list_reports` | limit | 报告列表 | read:sensitive | history_service |
| `pipeline_status` | run_id | pipeline_runs 状态 | read:status | 第 2 章产物 |
| `trigger_analysis` | mode/stock_list/force | run_id(异步) | write:trigger | run_flow(第 2 章管线入口) |
| `cancel_run` | run_id | 取消结果 | write:trigger | 第 2 章编排器 |

- 工具函数薄封装现有 service;输入/输出用 pydantic v2 模型 + docstring description(客户端自动发现)
- 服务端每工具执行前二次断言 scope

### 5. 接缝协议

- **限流**:key 级 QPS(默认 10/s,可配)+ trigger_analysis 专用 1/min
- **并发协调**:trigger_analysis 走 pipeline 单例锁(同 mode+date 仅一个 run),force=True 先标 superseded 再重跑(与第 2 章一致)
- **超时/缓存**:query_quote 5s 超时 + 200ms TTL 缓存;read_report 读 DB 不缓存
- **错误映射**:pydantic ValidationError → JSON-RPC -32602;内部服务错误 → -32603;鉴权失败 → -32001(自定义);显式映射表
- **审计字段**:key_id/client_name(可选手填)/remote_ip/tool_name/params_hash/耗时,走 run_diagnostics 脱敏链

### 6. 测试

- 无 key 时 404;有 key 时工具调用(注入假 service,app.dependency_overrides)
- "无 write scope 的 key 调 trigger_analysis → 403"(反向断言)
- trigger_analysis 返回 run_id 且不阻塞;参数校验错误 → JSON-RPC error
- 文档:客户端配置示例(.cursor/mcp.json / Claude Desktop)入 docs

---

# 跨章共享决策

| 决策 | 值 |
|---|---|
| pydantic 版本 | v2(项目 venv 2.13.4),契约/artifact/schema 全 v2 |
| artifact schema | 顶层 `schema_version: 1` |
| 审计/脱敏 | 复用 run_diagnostics.py 脱敏链 |
| 云端分支治理 | meta/runs 独立分支 + branches-ignore 校验 |
| 并发 | 各子系统单例锁,superseded 标记替代 |
| 测试 | 每子系统独立单测 + CI 三 shard(耗时平衡)+ 反向断言 |

# 风险登记册(v1 明确不做)

- 连接器:hot-reload 注册表
- MCP:发布者签名(cosign/minisign)、团队 RBAC、list_runs/list_universe
- 客户端:updater.exe 自更新、定时轮询、自动执行更新(手动重启替换)
- 管线:全 LLM 自主 agent(保持确定性编排)