# 打包 DSA 云端客户端为 onedir 可执行(windows)。
# 用法: pwsh ./apps/dsa-cloud-client/build.ps1
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
Set-Location $PSScriptRoot  # 脚本自身目录即 apps/dsa-cloud-client

if (-not (Test-Path "$PSScriptRoot\.venv")) { Write-Warning "未找到 .venv,请先在仓库根目录创建虚拟环境并安装 requirements" }

pyinstaller `
  --noconfirm `
  --clean `
  --onedir `
  --name dsa_client `
  --version-file version_info.txt `
  --add-data "static;static" `
  --hidden-import uvicorn.logging `
  --hidden-import uvicorn.loops.auto `
  --hidden-import uvicorn.protocols.http.h11_auto `
  --hidden-import uvicorn.protocols.websockets.auto `
  --collect-submodules dsa_client `
  app.py

Write-Host "build complete: dist/dsa_client.exe"
