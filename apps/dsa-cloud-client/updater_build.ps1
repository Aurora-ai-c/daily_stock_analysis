# 构建 updater.exe(onefile,仅标准库 + dsa_client.updater_apply)。
# 用法: pwsh ./apps/dsa-cloud-client/updater_build.ps1
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
Set-Location $PSScriptRoot  # 脚本自身目录即 apps/dsa-cloud-client

pyinstaller `
  --noconfirm `
  --clean `
  --onefile `
  --name updater `
  updater_entry.py

Write-Host "build complete: dist/updater.exe"