@echo off
REM Restore the latest versioned backup to the main exe (manual rollback tool).
REM Usage: restore.bat [backup_dir] [exe_path]
REM   default backup_dir = %CD%\data\backups, exe_path = %~dp0dsa-cloud-client.exe
REM Backups are named <version>_<exe-name>.bak; newest version wins (0.10.0 > 0.9.0).
REM NOTE: keep this file ASCII-only; cmd.exe parses batch files in the ANSI codepage.
setlocal
set "BACKUP_DIR=%~1"
set "EXE_PATH=%~2"
if "%BACKUP_DIR%"=="" set "BACKUP_DIR=%CD%\data\backups"
if "%EXE_PATH%"=="" set "EXE_PATH=%~dp0dsa-cloud-client.exe"
if not exist "%BACKUP_DIR%" (
  echo [restore] ERROR: backup dir not found: %BACKUP_DIR%
  exit /b 1
)
for /f "usebackq delims=" %%F in (`powershell -NoProfile -Command "$d = '%BACKUP_DIR%'; $b = Get-ChildItem -Path $d -Filter *.bak -File | Where-Object { $_.Name -match '^\d+\.\d+\.\d+_' } | Sort-Object { [version]($_.Name -replace '^(\d+\.\d+\.\d+)_.*', '$1') } -Descending | Select-Object -First 1; if ($b) { Write-Output $b.FullName }"`) do set "LATEST=%%F"
if "%LATEST%"=="" (
  echo [restore] ERROR: no versioned backup found in: %BACKUP_DIR%
  exit /b 1
)
copy /y "%LATEST%" "%EXE_PATH%" >nul
if errorlevel 1 (
  echo [restore] ERROR: copy failed, is the exe locked by a running process?
  exit /b 1
)
echo [restore] Restored from latest backup:
echo          %LATEST%
echo          -^> %EXE_PATH%