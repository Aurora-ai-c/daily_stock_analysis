# DSA 部署指南

本仓库交付两条并行的产品能力：

1. **本地优先桌面客户端** `apps/client`（本指南重点）——一台机器即可跑完「数据获取 → 分析 → 报告 → 推送 → 移动端查看」。
2. **服务端自托管**（Docker / GHCR 镜像、`deploy_user.py` 协作者模板）——面向需要在服务器 7×24 运行的场景，与客户端路线并行保留。

## 一、本地桌面客户端（apps/client）

统一后的客户端由三部分组成：

- `apps/client/electron`：Electron 外壳，本地拉起冻结后端、托管 Web、提供系统托盘与自动更新。
- `apps/client/web`：前端（React + Vite），经 `vite` 构建到仓库根 `static/`，由冻结后端直接托管。
- 后端：PyInstaller 冻结为单文件（`scripts/build-backend.ps1` / `build-backend-macos.sh` 产出 `dist/backend/stock_analysis`），由 Electron 外壳作为本地进程启动。

### 开发运行

```bash
# 1) 构建 Web
cd apps/client/web
npm ci
npm run build

# 2) 启动桌面壳（开发模式）
cd ../electron
npm ci
npm run dev
```

首次启动会通过向导配置 LLM key（经 Electron `safeStorage` 本地加密，绝不落盘明文），并可导入自选股。

### 生产构建 / 发布

- Windows：`scripts/build-backend.ps1` → electron-builder（NSIS 安装包 + `latest.yml` + blockmap）。
- macOS：`scripts/build-backend-macos.sh` → `scripts/build-desktop-macos.sh`（DMG，保持未签名构建以规避 #2075 缺陷）。
- 发版由 `desktop-release.yml` 在 `v*.*.*` tag 或 `workflow_dispatch` 时统一产出 Windows/macOS 产物并发布到 `Aurora-ai-c/daily_stock_analysis` Releases（main.js 不硬编码发布源，仅经 electron-builder `publish` 配置注入）。

### 本地 SearXNG 一键

设置页「系统 → SearXNG」可一键拉起本地 Docker 实例（`apps/client/searxng/`），作为私有元搜索后端；未启用时回退公共实例开关（默认关闭）。需本机已安装 Docker。

### 移动端远程访问

设置页「系统 → 远程访问」：

- 开启后后端监听 `0.0.0.0` 并强制 `ADMIN_AUTH_ENABLED=true`（请先在账户设置强密码）。
- 同一 Wi-Fi 下用「局域网地址」直接用手机浏览器打开。
- 公网用「启动公网隧道」经 cloudflared 生成一次性 `*.trycloudflare.com` 地址；手机扫码即可访问。
- 桌面端本身即为 PWA：非 Electron 壳打开时浏览器可「添加到主屏幕」，离线缓存由 Service Worker 提供。

## 二、服务端自托管（并行能力）

- 镜像：`ghcr.io/Aurora-ai-c/daily_stock_analysis`（见 `ghcr-dockerhub.yml`），或 `docker-compose.yml` 本地构建。
- 协作者模板分发：`python scripts/manage_collaborators.py` + `scripts/deploy_user.py`（读权限 PAT，用完即废）。
- 云端分析 workflow（`00-daily-analysis.yml.disabled`）保留但默认关闭，作回滚参考。

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 桌面端启动后白屏 | Web 未构建或端口被占 | 先 `npm run build`；确认 8000–8100 未被占用 |
| 后端起不来 | PyInstaller 产物缺失 | 重跑 `scripts/build-backend.ps1` |
| SearXNG 启用失败 | 未装 Docker / compose 超时 | 安装 Docker；compose 拉起超时（默认 180s）会报错而非挂起 |
| 远程访问连不上 | 未设管理员密码 / 防火墙 | 先设强密码；确认 `0.0.0.0` 监听与防火墙放行 |
