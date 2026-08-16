# DSA 平台升级四项:连接器抽象 / Agent 管线 / 客户端更新 / MCP 集成 — 设计文档

日期:2026-08-16(v2,吸收评审反馈)
状态:设计确认,待用户评审
实施顺序:**1 → 2 → 4 → 3**(依赖序)

## 背景与目标

借鉴 Fincept 的四个方向,按"严谨是流程属性,不是模型属性"的原则收敛为四项基础设施升级:

1. **连接器抽象**:统一 bar/quote/fundamental 契约 + 注册表 + 配置化启停
2. **Agent 化研究管线**:五步确定性管线(采集→探针→交叉验证→渲染→推送),每步独立产物 + 可审计日志
3. **客户端更新通道**:dsa-cloud-client exe 启动检查 + UI 横幅更新
4. **MCP 工具集成**:标准 MCP server(查询信号/触发分析/读报告),外部 AI 工具遥控协议

## 跨章共享决策(先读)

| 决策 | 值 |
|---|---|
| pydantic 版本 | v2(项目 venv 2.13.4),契约/artifact/schema 全 v2 |
| artifact schema | 顶层 `schema_version: 1` |
| 审计/脱敏 | 扩展 run_diagnostics.py 为 `DiagnosticRecord` 基类 + 子类(见下) |
| 云端分支治理 | meta/runs 独立分支 + branches-ignore 校验 |
| 并发 | 各子系统单例锁,superseded 标记替代 |
| 上线姿态 | **feature flag + 默认关闭**(CONNECTOR_V2_ENABLED / PIPELINE_V2_ENABLED / MCP_API_KEYS 未配置即 404) |
| 验证责任 | service 边界每层独立校验,不信任上游已校验 |
| CI 测试 | 按子系统切 shard(connector / pipeline / mcp+update)+ pytest-xdist + `slow` 标记隔离网络测试 |

### 审计脱敏扩展机制

`run_diagnostics.py` 扩展为基类 + 多态:
```
DiagnosticRecord(基类: to_dict / sanitize 共享)
├── FetcherDiagnostic       # 第 1 章(fetcher error/fallback)
├── PipelineStepDiagnostic  # 第 2 章(step run)
├── McpCallDiagnostic       # 第 4 章(MCP call)
└── UpdateEventDiagnostic   # 第 3 章(update event)
```
规则:新增事件类型必须继承 `DiagnosticRecord`,共享脱敏链。

---

# 第一章:连接器抽象

## 现状(已盘点)

- `data_provider/` 12 个 BaseFetcher + tw_institutional + 2 个 fundamental adapter
- `BaseFetcher`(base.py:331):日线列名已统一,含熔断 CircuitBreaker
- `UnifiedRealtimeQuote`(realtime_types.py:110,dataclass):已含 currency/market/fetched_at/is_stale/fallback_from
- `get_realtime_quote`(base.py:1726):硬编码 if/else 路由
- `get_fundamental_context`(base.py:3140):无统一形状
- `_DAILY_MARKET_FETCHER_SUPPORT`(base.py:619):启停硬编码
- 已有范本:`intelligence` 资讯源系统(/api/v1/intelligence)

## 设计

### 1. 契约层(data_provider/contracts.py,pydantic v2)

**关键决策:Quote 拆 raw/derived 两层**(fetcher 直接产出 vs 计算派生)

| 契约 | 字段 | 规则 |
|---|---|---|
| `Bar` | date/open/high/low/close/volume/amount/pct_chg/turnover_rate | 移除 ma5/ma10/ma20/volume_ratio(派生,调用方计算) |
| `Quote`(**raw**,fetcher 直接产出) | code/name/price/open/high/low/pre_close/volume/amount/change_pct(注释"较昨收")/change_amount/bid/ask/tz/currency/market/fetched_at/provider_timestamp/is_stale/stale_seconds/fallback_from/data_quality/missing_fields | 完整字段,缺省容忍(None 可) |
| `QuoteDerived`(**计算层**) | volume_ratio/turnover_rate/amplitude/pe_ratio/pb_ratio/total_mv/circ_mv/change_60d/high_52w/low_52w | 由 `QuoteDerivedCalculator` 组合 Quote + Bar + FundamentalRaw 产出 |
| `FundamentalRaw` | 三表关键科目 ~25 项 + `report_date: date` + `fiscal_period: Literal['Q1','Q2','Q3','Q4','FY']` + `market` | 分市场口径:A 股 akshare/tushare,美股 yfinance/finnhub |
| `FundamentalDerived` | 拆三组,标记依赖源:① 纯基本面派生(ROE/股息率)② 跨切股价派生(PE/PB)③ 历史窗口派生(52w 高低) | 与 QuoteDerived 无归属冲突 |

**兼容策略:**
- 新 `Quote` 与旧 `UnifiedRealtimeQuote` 共存期:新 endpoint 返回 Quote,旧 endpoint 返回旧 dataclass
- `Quote.legacy_compat()` 方法转回旧 dataclass;旧 dataclass 标 deprecation warning
- `BaseFetcher` 顶层加抽象方法 `to_quote()` / `to_bar()` / `to_fundamental()`,现有子类逐步实现(先 4 目标源,其他 enabled: false)

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
    health_check: null        # "module:function" 字符串,def health_check() -> bool
    version: "1"
```

`FetcherSpec` 字段:`name/module/class/markets/capabilities: list[Literal['quote','bar','fundamental']]/priority/enabled/rate_limit/timeout/env_required/health_check/version`。

`discover_fetchers()` 流程:
1. 读 config/fetchers.yaml
2. pydantic `FetcherSpec` 校验整体结构
3. `importlib.import_module(spec.module)` 验证可导入
4. 验证 `spec.class` 是 module 里的类
5. `health_check` 解析 `module:function`,启动时调用一次,失败 warn-only 禁用
6. 返回强类型 list

**启动策略**:class 无法导入 = fail-fast;env_required 缺失 = warn-only 降级;health_check False = warn-only 禁用。hot-reload v1 不做。

**本地/云端配置**:共享一份 fetchers.yaml,不分文件。云端 Actions 环境无 TUSHARE_TOKEN 等 → env_required 自动禁用对应源(文档化此行为)。

### 3. DataFetcherManager 改造

- 删除硬编码 `_DAILY_MARKET_FETCHER_SUPPORT`(base.py:619)与 if/else 路由(base.py:1726)
- 改为读注册表按 `capabilities` + `markets` + `priority` 路由
- `_try_fetcher_quote` 签名改为 `(spec: FetcherSpec, code: str, ...) -> Quote | None`;registry 通过 DI / module-level singleton 注入
- 熔断器保留

### 4. 调用点切换

已盘点风险分级:

| 影响级 | 位置 |
|---|---|
| 高 | `src/core/pipeline.py`(~30 处 getattr)、`src/agent/executor.py`、`src/analyzer.py:947` |
| 中 | `src/agent/events.py`、portfolio/risk/technical agent、`screening_service.py:3358`、`dsa_provider.py:96`、`data_tools.py:493`、`pipeline.py:530` |
| 低 | 各 agent tools |

迁移顺序:低影响先行,高影响后行,每批跑对应测试。

### 5. 上线与测试

- **feature flag `CONNECTOR_V2_ENABLED`(默认 false)**;旧路径保留直至 flag 切 true 并验证
- 删除 `_DAILY_MARKET_FETCHER_SUPPORT` 前:**grep 兜底确认无任何调用方**
- v1 契约适配:akshare/tushare/yfinance/finnhub 四源;其余 10 个注册表化 + `enabled: false` 逐个补测
- 测试:四源契约适配(shape 断言 + 缺省容忍)、registry 校验(class 不存在 fail-fast / env 缺失 warn-only)、路由(capabilities 过滤)、现有回归全量保留

---

# 第二章:Agent 化研究管线

## 现状(已盘点)

- 已有 `src/agent/agents/`(五 agent)、`agent/skills/`、`agent/tools/`
- `decision_signal_extractor.py:201`:分析后 LLM 信号提取(输入 AnalysisResult,下游消费者)
- `backtest_service.py`:信号后验闭环
- `run_diagnostics.py`:ProviderRun/LLMRun + 脱敏链
- 云端 `00-daily-analysis.yml`(单 job + heartbeat,meta/heartbeat 分支 worktree push)

## 设计

### 1. 五步管线(src/services/pipeline/ 新目录)

| 步骤 | 服务 | 产物(JSON) | 资产复用 |
|---|---|---|---|
| 1. 数据采集 | `collector.py` | `{fetchers_used, rows, missing_markets[], latency}` | 第 1 章连接器统一入口 |
| 2. 信号探针 | `probe.py` | `{candidates, signals[], probe_score}` | 新写(见下) |
| 3. 交叉验证 | `cross_validator.py` | `{confirm[], conflict[], resolution}` | backtest_service + outcome_service |
| 4. 报告渲染 | `renderer.py` | `{report_path, format, render_latency}` | report_renderer.py(Jinja2) |
| 5. 推送 | `pusher.py` | `{channels[], per_channel_status, failures[]}` | notification_sender/ |

### 2. probe 信号设计(评审 Top #3,独立子任务)

**v1 最小信号集(6 个技术信号,每信号算法定义 + 阈值表):**

| 信号 | 算法 | 默认阈值 |
|---|---|---|
| 均线交叉 | MA5 上穿/下穿 MA20 | 交叉当日 |
| 量比异常 | volume / 前 5 日均量 | > 2.0(放量)或 < 0.5(缩量) |
| 突破 N 日高低 | close 突破 20 日最高/最低 | 20 日窗口 |
| 涨跌幅异动 | pct_chg 相对近 20 日分布 | |z| > 2 |
| 资金流异常 | 大单净流入占比(有源则用) | > 30% |
| 量价背离 | 价升量缩 / 价跌量增 | 3 日持续 |

- probe 信号 `source="probe"`;extractor 信号 `source="llm"`;backtest 后验 `source="backtest"` — **三类信号统一 `Signal(source, code, direction, confidence, timestamp, ...)` schema**,cross_validator 显式标注投票
- `probe_score` 公式(写入 probe.py docstring):`sum(signal_confidence * weight) / sum(weights)` 归一化 0-1
- 算法配置:`config/probe.strategies.yaml`(独立于策略库,便于扩展)

### 3. cross_validator(评审决议)

- 输入三路:probe signals + extractor signals(输入仍为 AnalysisResult,不变)+ backtest outcomes
- **resolution v1 策略**:confirm 数 > conflict 数 → `confirmed_via_majority`;反之 `rejected_via_majority`;相等 → `tie_pending_review`(结构化字段,非自由文本;tie 场景 banner"待人工复核")
- 输出:cross-validated signals 列表 → renderer
- backtest_service 接受任意 source 信号;outcome_service T+1 复查记录当时 source

### 4. 编排器(pipeline_engine.py)

- 新表 `pipeline_runs`(run_id/trigger/mode/date/status/started_at/completed_at/error_summary/superseded_by)+ `pipeline_steps`(run_id/step/status/artifact_path/latency_ms/error/degraded_reasons)
- 步骤间产物:结构化 JSON + pydantic v2 schema,顶层 `schema_version: 1`
- 可回放:同一 run_id 可重跑任一步

### 5. ReplayMode 协议

```python
class ReplayMode(Enum):
    FORWARD_ONLY = "forward_only"          # 首跑:全步骤
    SIDE_EFFECT_FREE = "side_effect_free"  # 重跑 1-4,跳过 side_effects=True 步骤
    DRY_RUN = "dry_run"                    # 全跑,push 走 mock
```

**`side_effects` 精确判定标准(评审决议)**:side_effect = **外部可观测副作用**(通知发送 / 花钱 / 限流配额消耗)。
- renderer 写本地文件:**不算**(artifact 本就该有)
- collector 调 API:**算**(消耗 rate limit quota)
- probe 纯计算:**不算**
- cross_validator 内部 DB 写:**算**(影响后续统计)
- pusher:**算**(唯一 True,重跑跳过)
- 重跑时 side_effects=True 的步骤跳过或 mock;renderer 产物写 `step_<n>.<seq>.json` 不覆盖;replay 只对已失败 run_id 开放

### 6. 失败语义

- **hard-fail 仅步骤 1**:全部市场无数据才中止
- 步骤 1 部分失败:per-market 降级,`degraded=True, missing_markets=[...]`,渲染层标"⚠️ 数据缺失"
- 步骤 2-5 soft-fail:`degraded_reasons[]`,继续流转
- probe 失败:报告加"⚠️ 信号生成失败,本次仅基础分析"横幅
- cross_validator 失败:信号 `confidence="unverified"`,禁止无标注展示

### 7. 时序语义(已确认)

当日发信号 → backtest 1/3/5 日窗口(eval_window_days)→ outcome_service T+1 复查。

### 8. 产物落点与云端回写(评审决议)

- 本地(单一源):`data/pipeline/runs/<run_id>/step_<n>_<step_name>.json`
- 云端镜像:`meta/runs/<date>/<run_id>.json` — **由独立 workflow `runs-mirror.yml` 消费产物回写**(非引擎直接 push),与 heartbeat 共用 worktree push 模式但独立 `meta/runs` 分支
- 回写失败仅 warn(pipeline 本地已成功,镜像失败不阻塞);`meta/runs` 保留 90 天
- `branches-ignore: [meta/runs, meta/heartbeat]` CI 校验
- 共享 step 抽到 composite action / reusable workflow,避免 00-daily-analysis.yml 继续膨胀

### 9. 并发治理

- `concurrency_key = mode + date` 单例锁
- superseded 链:同日多次触发标记旧 run;**链长度上限 5**,超限删除最旧;按 date 索引 + 保留 latest 指针,回溯查询只查 latest(v1)

### 10. 其他

- pusher 重试:指数退避 3 次(1s/4s/16s),per-channel 独立
- DI 测试:`step_registry.py` + `StepFactory`
- 迁移:feature flag `PIPELINE_V2_ENABLED`(默认 false),旧流程保留并行
- 诊断:新增 `PipelineStepDiagnostic`(继承 DiagnosticRecord)

---

# 第三章:客户端更新通道

## 现状(已盘点)

- `apps/dsa-cloud-client/`:PyInstaller onedir,`version_info.txt` 0.1.0.0,`app.py:main()` 单进程
- `dsa_client/github_client.py`:PAT 调 GitHub API
- `dsa_client/config.py`:DPAPI 加密 PAT,`~/.dsa-cloud/`
- `dsa_client/server.py`:token/origin 守卫
- `desktop-release.yml`(Electron 版 latest.yml,与 PyInstaller 客户端无关)

## 设计

### 1. 发布流程(新增 .github/workflows/client-release.yml)

- manual dispatch 入参:`release_tag`(vX.Y.Z)+ `channel`(stable)+ `platform`(win)+ `arch`(x64)
- 校验 tag → checkout → 构建 → zip + sha256 → updates.json → GitHub Release
- dry-run 模式:仅本地构建 + schema 校验,不上传资产
- **可重现构建**:CI build 启用 SOURCE_DATE_EPOCH;onedir 排除 `*.pyc` 与 `.git/`;zip 内含 `BUILD_INFO.txt`(commit SHA + 构建时间 + runner)
- 共享 step(zip + sha256 + release creation)抽到 composite action,与 desktop-release.yml 复用

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

客户端按 `sys.platform` + `platform.machine()` 匹配;v1 仅 win-x64 stable 产出。

### 3. 客户端检查(dsa_client/updater.py 新增)

- 归属:exe 主进程后台线程(daemon),`main()` 中 uvicorn.run 之前启动,不经 FastAPI、不经 server 守卫;线程退出清 PAT 引用
- 检查源:`github_client.get_latest_release()` 读 updates.json
- 版本比较:`packaging.version`(PEP 440);v1 仅 stable,不识别 prerelease,忽略 build metadata
- 结果缓存 24h(`~/.dsa-cloud/update_cache.json`);网络异常重试 2 次(指数退避),最终静默;PAT 失效/无网 → 静默跳过

### 4. 版本单源真相(评审决议)

- **dispatch release_tag 为唯一真相源**
- 构建期生成 `dsa_client/_version.py`(dev 用)+ 同步 version_info.txt(PE 资源)
- **运行时优先级**:frozen exe 优先 PE 资源(唯一可信源);`_version.py` 仅 dev 用
- 构建脚本断言:生成 `_version.py` 后立即验证 PE 资源版本一致
- startup 校验:`_version.py` 与 PE 资源不一致 → fail-fast

### 5. 原子替换流程(updater.exe 子进程模式)

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
  → 失败:杀掉 → 回滚 → 重启旧版 → 写 update.log
```

- 跨进程通信:CLI 参数 + 退出码
- 替换后首次启动 `_version_installed` 写 config,横幅"已更新到 vX.Y.Z"
- 用户自助恢复:`restore.bat`(`~/.dsa-cloud/`,文档化);updater.exe 自更新记录风险册

### 6. UI 与状态机

- 顶栏横幅 + 下载按钮 + 进度;状态机 `idle → checking → downloading → extracting → ready → restart_pending`(按钮按状态禁用)
- 取消 = 删除已下载,回 idle;磁盘预检 ≥ 1.5× zip;解压临时目录 → 校验 → 原子 rename;update.log 全程记录

### 7. 安全

- sha256 完整性校验(强制);下载走 GitHub release 资产(https);发布者签名 v1 不做(风险册);24h 检查缓存限流

### 8. 测试

- updater 单测:semver 比较、数组匹配、sha256 失败、回滚逻辑(mock)、状态机
- workflow dry-run:schema 校验;UI 横幅 JS 单测(有/无更新)

---

# 第四章:MCP 工具集成

## 现状(已盘点)

- 项目 venv 无 mcp 模块(需新增依赖)
- `AuthMiddleware`(api/middlewares/auth.py:37)cookie session,仅挡 /api/v1/*
- FastAPI 入口 `api/app.py`;`src/auth.py`
- 已有 `_try_acquire_market_review_lock`(每日分析锁)

## 设计

### 1. 部署形态(评审 Top #2 决议)

- **MCP 仅 local 部署**(本地/自部署后端 `api/app.py` 内嵌,`app.mount("/mcp", ...)`);**cloud 不挂 MCP**
- 文档化:"MCP 是本地遥控协议;云端通过 deploy_user.py 触发 GitHub Actions"
- 本地无既有 MCP 服务,全新部署(非扩展)
- v1 默认仅 127.0.0.1;远程部署文档化(前置 nginx 限流)
- AuthMiddleware 边界:MCP 路径自带 key 校验;OpenAPI 标注"/mcp/* 独立鉴权"
- 生命周期:mcp 随 FastAPI lifespan 启停,无额外清理

### 2. 依赖治理

- pin `mcp==1.2.x`(锁 minor);CI 加 MCP 工具 happy-path 冒烟;季度升级评审

### 3. 鉴权(多 key + scope + key_id 审计)

```yaml
MCP_API_KEYS: '{"key_alice":"<sha256>","key_bob":"<sha256>"}'   # key_id → key 哈希
MCP_KEY_key_alice_SCOPE: "read:basic,read:sensitive"
MCP_KEY_key_bob_SCOPE: "read:basic,read:sensitive,write:trigger,read:status"
```

- sha256 哈希存储;每 key 独立审计标签;未配置 → 404(默认关闭);轮换流程文档化
- 限流:令牌桶,每 key 独立计数器;默认 10/s;trigger_analysis 专用 1/min

### 4. 工具集(v1 共 8 个)

| 工具 | 输入 | 输出 | scope | 底层服务 |
|---|---|---|---|---|
| `query_quote` | code | Quote(第 1 章契约) | read:basic | DataFetcherManager |
| `query_fundamental` | code | Fundamental 契约 | read:sensitive | 第 1 章产物 |
| `query_signal` | code/date/limit | decision_signal + 后验 | read:sensitive | DecisionSignalService + backtest_service |
| `read_report` | date/type/market | 报告 markdown | read:sensitive | report_renderer / history_service |
| `list_reports` | limit | 报告列表 | read:sensitive | history_service |
| `pipeline_status` | run_id | pipeline_runs 状态 | read:status | 第 2 章产物 |
| `trigger_analysis` | mode/stock_list/force | run_id(异步) | write:trigger | run_flow(第 2 章管线入口) |
| `cancel_run` | run_id | 取消结果 | write:trigger | 第 2 章编排器 |

**schema 纪律(评审决议):**
- MCP 工具输入/输出模型**必须 import 自 `data_provider/contracts.py`**,不允许复制定义
- CI 校验:MCP 工具 schema ⊆ REST schema(避免字段漂移)
- 工具 description:统一 pydantic `Field(..., description="中文")` + 函数 docstring(Markdown 含示例);CI 校验所有工具均有 description;FastMCP 从 Field 自动生成 list_tools
- **与 REST 边界**:MCP 是"对外(AI 客户端)"协议层,不替代 REST;既有 /api/v1/* 是"对内(前端)"接口;共享 service 层,路由独立

### 5. 接缝协议

- **并发协调**:trigger_analysis 复用 `_try_acquire_market_review_lock`(同一把锁,同一 mode+date),不新建锁;force=True 先标 superseded 再重跑
- **超时/缓存**:query_quote 5s 超时 + 200ms TTL 缓存;read_report 读 DB 不缓存
- **错误映射**:ValidationError → -32602;内部服务错误 → -32603;鉴权失败 → -32001
- **审计字段**:key_id/client_name(可选)/remote_ip/tool_name/params_hash/耗时 → `McpCallDiagnostic`
  - `params_hash = sha256(json.dumps(params, sort_keys=True))[:16]`
  - **stock_list 全量不进日志,仅入 hash**(避免 PII);审计保留 90 天

### 6. 测试

- 无 key → 404;有 key 工具调用(注入假 service,app.dependency_overrides)
- 反向断言:无 write scope 调 trigger_analysis → 403
- trigger_analysis 返回 run_id 不阻塞;参数校验 → JSON-RPC error
- 文档:客户端配置示例(.cursor/mcp.json / Claude Desktop)入 docs

---

# 兼容性清单(与现有系统)

| 组件 | 策略 |
|---|---|
| `UnifiedRealtimeQuote`(realtime_types.py:110) | 共存期:新 endpoint 返回 Quote,旧返回旧 dataclass;`Quote.legacy_compat()` 转回;deprecation warning |
| `BaseFetcher`(base.py:331) | 顶层加 `to_quote()/to_bar()/to_fundamental()` 抽象;先 4 目标源实现,其他 enabled: false |
| `get_realtime_quote` if/else( base.py:1726) | 删 if/else;`_try_fetcher_quote(spec, code, ...)`;registry DI/singleton |
| `decision_signal_extractor.py:201` | 输入不变(AnalysisResult);移入 cross_validator 作为三路输入之一 |
| `backtest_service` / `outcome_service` | Signal 统一 schema(source/code/direction/confidence/timestamp),接受任意 source |
| `run_diagnostics.py` | 扩展为 DiagnosticRecord 基类 + 子类 |
| `00-daily-analysis.yml` | meta/runs 回写独立 workflow(runs-mirror.yml);共享 step 抽 composite action |
| `_try_acquire_market_review_lock` | trigger_analysis 复用同一把锁 |
| `desktop-release.yml` | zip + sha256 + release 共享 step 抽 composite action,复用 |
| 云端 fetcher 配置 | 共享 fetchers.yaml + env_required 自动降级,不分文件 |

# 风险登记册(v1 明确不做)

- 连接器:hot-reload 注册表、运行时 health_check 定时检查(30min)
- MCP:发布者签名、团队 RBAC、list_runs/list_universe
- 客户端:updater.exe 自更新、定时轮询、自动执行更新(手动重启替换)
- 管线:全 LLM 自主 agent(保持确定性编排)

# 实施计划拆分建议

1. **part-a 连接器抽象**:contracts v2 + registry + 四源适配 + 调用点迁移 + CONNECTOR_V2_ENABLED
2. **part-b Agent 管线**:pipeline_engine + 五步(probe 独立子任务)+ 产物/审计 + PIPELINE_V2_ENABLED
3. **part-c MCP**:依赖 mcp==1.2.x + 多 key 鉴权 + 8 工具 + 冒烟测试
4. **part-d 客户端更新**:updater.py + client-release.yml + updater.exe + UI 横幅