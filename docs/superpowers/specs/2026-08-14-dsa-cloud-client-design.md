# DSA 云端远程控制客户端(exe)设计

日期:2026-08-14(评审修订 v2:2026-08-15)
状态:已批准(用户确认)

## 背景与目标

DSA(每日股票分析)已有本地分析 + GitHub Actions 云端定时分析双链路,云端报告可注入 AlphaEvo 策略信号章节(默认开启,含与 LLM 结论的支撑/分歧标注——该功能已在本设计前的迭代中完成实现)。

本设计解决新的产品化诉求:**把该能力交付给其他使用者**。每位使用者拥有自己独立的 GitHub 云端(私人仓库 + Actions),通过一个 Windows exe 客户端远程管理自选股、配置密钥、触发分析、查看报告与信号,并监控 Actions 配额。

核心决策(经多轮澄清确认):
- **形态**:每人独立一套(各自 GitHub 账号 + 私人仓库 + Actions)
- **分发**:GitHub Template 仓库 + 授权(private template,"Use this template" 生成独立私有仓库,**无 fork 关系**)
- **exe 界面**:本地 FastAPI Web 服务 + 浏览器(FastAPI 技术栈,独立新模块,不耦合 DSA 全量设置页)
- **认证**:每用户经典 PAT(repo + workflow,user 可选;2FA 用户须 fine-grained PAT)
- **部署**:`scripts/deploy_user.py` + `docs/DEPLOYMENT.md`,由部署 agent 用用户 PAT 执行
- **自选股**:仓库变量 `STOCK_LIST`(定时默认)+ dispatch 参数覆盖(手动触发)
- **报告/信号**:GitHub Actions artifacts(`reports/` 含信号章节 + `strategy_signals_latest.json` 诊断)

## 架构

```
[exe: PyInstaller 打包 Python(onedir)]
  ├── 本地 FastAPI 服务(绑定 127.0.0.1;端口 49152-65535 动态区随机,
  │     占用则重试换端口(≤3 次);启动/运行日志写 ~/.dsa-cloud/server.log)
  │     ├── 前端:登录/自选股/云端设置/触发运行/报告与信号/配额
  │     ├── 安全(阶段 1):绑定 127.0.0.1 + 启动随机 token 写入 URL hash + Origin 校验
  │     ├── /health 端点:浏览器自动打开失败时的连通性自检
  │     └── 浏览器打开失败兜底:终端打印完整 URL(含 token)+ 手动复制入口
  ├── GitHub 客户端模块(requests 调 REST API;429 时重试 + exponential backoff)
  │     ├── 认证:PAT 本地存储,Windows DPAPI 加密(阶段 1,标准库 ctypes 调 CryptProtectData)
  │     ├── 自选股:PATCH /repos/{owner}/{repo}/actions/variables/STOCK_LIST
  │     ├── 触发:POST /repos/{owner}/{repo}/actions/workflows/00-daily-analysis.yml/dispatches
  │     ├── 触发前检查:GET /actions/runs 确认无重复运行(UX 提示,并发兜底靠 workflow concurrency)
  │     ├── 报告:GET /actions/runs → /actions/runs/{id}/artifacts → 下载 zip 解析
  │     └── 配额:GET /user/settings/billing/actions(降级链见下)
  └── 本地配置:~/.dsa-cloud/config(DPAPI 加密的 PAT + owner/repo + 启动随机 token)
```

## 功能规格

### 1. 登录
- 输入 GitHub 用户名 + 经典 PAT(权限 `repo` + `workflow`;`user` 可选,仅配额卡片完整显示需要)
- 校验:`GET /user` + `GET /repos/{owner}/{repo}` 确认仓库存在与权限
- PAT 持久化到本地配置(DPAPI 加密);401 时提示重新登录

### 2. 自选股管理
- 增删改列表 → `PATCH /actions/variables/STOCK_LIST`(更新定时运行默认)
- 读回校验(PATCH 后 GET 确认)
- 手动触发时可选择"本次覆盖自选股"(不写变量)

### 3. 触发运行
- `POST .../dispatches`,body `{ref: "main", inputs: {mode, stock_list?, force_run}}`
- 触发后轮询 `GET /actions/runs` 展示运行状态(排队/进行中/成功/失败)

### 4. 报告与信号查看
- 列出最近 N 次成功运行 → 下载对应 artifact(`analysis-reports-*`)zip
- 解析 `reports/*.md` 渲染为 Markdown(含「🧭 策略信号」章节与支撑/分歧标注)+ 展示 `strategy_signals_latest.json` 关键字段
- 前端渲染:markdown → HTML 前经 DOMPurify 清洗 + 响应带 CSP 头(防上游模板被篡改注入 XSS);阶段 2 可选 iframe sandbox
- **信号卡片最小字段集**(与既有 schema 对齐,客户端只读这些字段):`{symbol, as_of_date, strategy, action, entry_price, stop_loss, target_price, confidence, supports, conflicts}`;字段变更属破坏性变更,需迁移指引
- 本地归档:报告页提供"下载到本地"按钮,zip 存 `~/.dsa-cloud/archive/<repo>/<date>.zip`(缓解 artifact 90 天过期)
- 已知边界:artifact 保留 90 天,过期不展示(本地归档后仍可查历史)

### 5. 云端设置(阶段 2)
- LLM 渠道(key/model)、通知 webhook 表单 → `GET /actions/secrets/public-key` → PyNaCl(libsodium)加密 → `PUT /actions/secrets/{name}`
- 仅写入用户自己的仓库;密钥不落本地

### 6. Actions 配额卡片(阶段 2)
- 三层降级:
  1. 经典 PAT 含 `user`:`GET /user/settings/billing/actions` → 显示免费额度/已用/剩余(private 免费 2000 分钟/月,UTC 月重置)
  2. 仅 repo 权限:`GET /repos/{owner}/{repo}/actions/usage` → 仅显示仓库已用分钟
  3. 403 等失败:显示引导文字(去 GitHub billing 页查看)+ tooltip 提示"需 PAT 勾选 `user` 权限以显示完整配额"
- 阈值提示:剩余 <10% 黄色警告,≤0 红色提示(免费账号超限后 Actions 暂停至月重置)

## 配套改动(模板仓库内)

### A. workflow `00-daily-analysis.yml`
1. **dispatch 增加 `stock_list` 输入**(可选字符串,触发时覆盖 `STOCK_LIST_CONFIG`);取值优先级:dispatch `stock_list` > 仓库变量 `STOCK_LIST` > 既有默认(保留旧 `STOCK_LIST_CONFIG` 兼容逻辑,触发时打 deprecation warning)
2. **heartbeat 步骤**(分析完成后):
   - job 单独声明 `permissions: contents: write`(workflow 顶层保持 read-only 默认,**不做仓库级放权**)
   - 写 `data/heartbeat_<YYYY-MM-DD>`(内容:时间戳、run_id、模式、结论摘要)
   - 维护 `data/heartbeat_health.json`(最近成功/失败时间戳,供 exe 启动时提示"最近心跳:X 天前")
   - git config(user.name=dsa-heartbeat[bot] 等)→ commit → push 到**专用分支 `meta/heartbeat`**(`git add -f` 强制添加被 .gitignore 忽略的 heartbeat 文件)
   - 失败不阻塞(warn + 写 health 失败记录);自触发防护已核实:本 workflow 无 push 触发;`auto-tag.yml` 限定 main+paths-ignore、`desktop-release.yml` 仅 tag、`ci.yml` 仅 PR → heartbeat 分支 push 不触发任何其他 workflow;**新增 push 类 workflow 必须带分支过滤**(文档化约束,阶段 2 加 CI 校验)
   - 效果:每次成功运行自动"续命",60 天无 commit 自动禁用规则永不触发;git 历史 = 诊断时间线
3. `.gitignore`:`/data/heartbeat_*` 忽略(main 分支不残留;heartbeat 分支靠 `git add -f` 强制提交)

### B. 部署脚本 `scripts/deploy_user.py`(由部署 agent 用用户 PAT 执行)
流程(幂等,支持 --dry-run):
1. 前置检查:模板仓库可见性与模板标记;用户 PAT 权限校验(PAT 需对模板仓库有读权限——用户须已先被添加为模板仓库协作者)
2. 生成用户仓库:`POST /repos/{template_owner}/{template_repo}/generate`,body `{"owner": <用户>, "name": <仓库名>, "private": true}`(幂等:已存在则跳过)
3. 启用 Actions:`PUT /repos/{owner}/{repo}/actions/permissions {"enabled": true}`;**不改** `default_workflow_permissions`(保持 read-only;heartbeat 靠 job 级权限声明)
4. 写 secrets(LLM key、通知 webhook;值由用户提供或表单输入)与变量(`STOCK_LIST` 默认):**默认 merge(仅写不存在的),`--overwrite-secrets` 才覆盖**;`PUT /actions/secrets/{name}` 语义为始终覆盖,文档标注
5. `--heartbeat-test`:部署成功后触发一次最小运行验证 heartbeat 闭环
6. 输出使用指引(创建/粘贴 PAT、打开 exe)
配套脚本(同目录):
- `scripts/manage_collaborators.py`:模板仓库协作者增/删/查(user 增删、角色查看),批量部署前置
- `scripts/batch_deploy.py`(**可选增强**,等用户量确认后实现):读 CSV(用户/token/邮箱)批量调用 deploy_user.py,含 backoff 与并发限速
### C. 文档 `docs/DEPLOYMENT.md`
- 授权流程(管理员添加协作者、private template 的 "Use this template" 入口)
- 部署脚本两种模式:用户自部署(交互粘贴 PAT)/ 代理部署(用户交付 PAT,注明授权边界)
- **PAT 安全必填章节**:最小权限清单(repo+workflow)、2FA 用户如何创建 fine-grained PAT(含步骤/截图)、凭证泄露应急流程(立即 revoke)
- 常见故障排查(配额超限、cron 未触发、heartbeat 失败、本地端口/防火墙、server.log 指引)

## 安全边界

- PAT 与仓库标识本地存储,经 **Windows DPAPI 加密**(阶段 1;标准库 ctypes 调 CryptProtectData,不引新依赖);解密仅限当前 Windows 用户、当前机器
- 本地服务**绑定 127.0.0.1**(防 DNS rebinding)+ 启动生成随机 token 写入 URL hash(访问凭证)+ Origin 校验(防恶意本地服务跨站请求),均在阶段 1
- 密钥经 GitHub secrets 加密通道写入,不落本地
- workflow 权限:顶层保持 read-only 默认,**仅 heartbeat job 级声明 `contents: write`**(不做仓库级放权,缩小 compromise 影响面)
- 前端 markdown 渲染经 DOMPurify 清洗 + CSP 头(防篡改模板注入 XSS)
- 使用者仓库为 private:自选股、报告、heartbeat 不外泄
- 文档标注 PAT 最小权限、2FA fine-grained PAT、泄露应急(revoke)

## 明确不做(YAGNI/规模待定)

- 不做 OAuth device flow(阶段 1;后续按需评估)
- 不做多用户共享云端/服务端托管
- 不耦合 DSA 全量设置页(仅远程控制功能;阶段 2 提供"导入本地配置差异"只读提示)
- 不做 fork 合并更新(use this template 无上游关系;更新同步走阶段 2 的"对比模板 diff + push 覆盖")
- `batch_deploy.py` / telemetry(Sentry)/ i18n 框架 / watchdog / 自动更新 / 多账号 profile → 均标注可选,阶段 2 视规模与需求决定

## 分阶段

**阶段 1(MVP)**
- workflow:dispatch `stock_list` 输入 + heartbeat(含 health、job 级权限、.gitignore + `git add -f`)
- `scripts/deploy_user.py`(generate/启用/merge-secrets/--heartbeat-test/指引)+ `scripts/manage_collaborators.py` + `docs/DEPLOYMENT.md`
- exe:本地 FastAPI(绑定 127.0.0.1 + token hash + Origin + 端口重试 + server.log + /health + 浏览器兜底)
- 登录/自选股/触发(前置查 runs)/报告读取(DOMPurify + 归档下载 + 最小字段集)四页
- 安全最小集:DPAPI 加密 PAT、429 backoff

**阶段 2**
- 云端设置页(secrets 加密写入)
- 配额卡片(三层降级 + tooltip)
- 模板更新同步(对比模板 diff + push 覆盖目标目录)
- PyInstaller 打包优化(onedir + version-file + 图标)、界面打磨、信号卡片图标+文字可读性(i18n 框架预留、a11y label)
- 心跳状态前端展示(最近心跳:X 天前)、heartbeat 分支清理脚本、CI 校验新 workflow 分支防护约束
- 视规模决定:多账号 profile、检查更新、watchdog、batch_deploy、telemetry 可选

## 测试策略

- workflow 改动:YAML 语法校验 + 本地可运行步骤检查;heartbeat 在测试仓库实测(一次性,含 heartbeat_health 写入)
- deploy_user.py:--dry-run 全链路模拟;API 调用 mock 化单测(含 merge/overwrite secrets 分支);--heartbeat-test 演练
- manage_collaborators.py:增删查 API mock 单测
- exe GitHub 客户端:requests mock 层单测(自选股 PATCH、dispatch、artifacts 解析、429 backoff);DPAPI 加解密往返单测
- 端到端:用测试用户账号 + 测试仓库演练完整部署 → 触发 → 查看报告闭环(阶段 1 验收)

## 已知边界(接受,不视为缺陷)

- artifact 90 天过期:报告页本地归档缓解,历史仍可回溯
- private 仓库免费 2000 分钟/月(日常使用约 22%,超限暂停至月重置,配额卡片预警)
- cron 触发时间为 UTC 10:30(北京 18:30),不保证精确到秒
- 并发兜底依赖 workflow `concurrency`(已存在于 `00-daily-analysis.yml`);exe 触发前查 runs 仅作 UX 提示,不承诺严格互斥
