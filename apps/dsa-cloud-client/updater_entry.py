# -*- coding: utf-8 -*-
"""updater.exe 入口:下载校验 → 备份 → 原子替换 → 健康检查 → 回滚。

用法:
  updater.exe --current-version X --target-version Y --url U --sha256 H --backup-dir B

可选项:
  --exe-path              待替换的主程序 exe 路径;默认取环境变量 DSA_APP_EXE,
                          再退化为与 updater.exe 同目录的 dsa_client.exe
  --dry-run               只做下载 + sha256 校验,不做备份/替换/健康检查(冒烟用)
  --health-check-seconds  替换成功后自身存活检查秒数(默认 30;自身存活即视为成功)

流程:下载到临时文件 → sha256 比对 → 备份当前 exe 到 B/(LRU 3 版,
命名 <version>_<exe名>.bak)→ 原子替换 os.replace → 不重启父进程 →
健康检查;失败恢复备份,并提示手动运行 restore.bat。
日志写入 data/update_log.txt(相对当前工作目录,与 updater.py 缓存同约定)。
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from dsa_client.updater_apply import plan_backup, verify_sha256

LOG_FILE = Path("data/update_log.txt")
DEFAULT_EXE_NAME = "dsa_client.exe"
HEALTH_CHECK_SECONDS = 30


def _ensure_console() -> None:
    """Windows GBK 控制台下 emoji 打印会 UnicodeEncodeError:以 replace 兜底。"""
    if sys.platform.startswith("win"):
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(errors="replace")
            except (AttributeError, ValueError):
                pass


def _logger() -> logging.Logger:
    """返回写入 data/update_log.txt 的 logger(幂等初始化)。"""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("updater")
    if not logger.handlers:
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def resolve_exe_path(exe_path: str | None) -> Path:
    """确定待替换的主程序 exe 路径:显式参数 > 环境变量 > 同目录默认名。"""
    if exe_path:
        return Path(exe_path)
    env = os.environ.get("DSA_APP_EXE")
    if env:
        return Path(env)
    sibling = Path(sys.argv[0]).resolve().parent / DEFAULT_EXE_NAME
    if sibling.exists():
        return sibling
    raise RuntimeError("无法确定待替换 exe:请传 --exe-path 或设置环境变量 DSA_APP_EXE")


def download_to_temp(url: str, target_dir: Path) -> Path:
    """流式下载到目标目录内临时文件(与 exe 同卷,保证 os.replace 原子性)。"""
    fd, tmp_name = tempfile.mkstemp(prefix=".update-", suffix=".tmp", dir=str(target_dir))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
            shutil.copyfileobj(resp, out, length=65536)
        return tmp
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def backup_exe(exe_path: Path, version: str, backup_dir: Path) -> Path:
    """备份当前 exe 到 backup_dir/<version>_<exe名>.bak。

    Windows 上运行中的 exe 可改名移动、不可覆盖:优先 os.replace(移动),
    失败(如文件被独占)则退化为 copy2。
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{version}_{exe_path.name}.bak"
    try:
        os.replace(exe_path, target)
    except OSError:
        shutil.copy2(exe_path, target)
    return target


def restore_backup(backup_path: Path, exe_path: Path) -> None:
    """恢复备份到 exe 位置(自动回滚;同样优先移动、退化为复制)。"""
    try:
        os.replace(backup_path, exe_path)
    except OSError:
        shutil.copy2(backup_path, exe_path)


def apply_update(args: argparse.Namespace) -> int:
    logger = _logger()
    exe_path = resolve_exe_path(args.exe_path)
    if not exe_path.exists():
        raise RuntimeError(f"待替换 exe 不存在: {exe_path}")
    exe_dir = exe_path.parent
    tmp = download_to_temp(args.url, exe_dir)
    try:
        if not verify_sha256(str(tmp), args.sha256):
            logger.error("sha256 校验失败,已丢弃下载文件: %s", tmp)
            print(f"❌ sha256 校验失败,目标版本 {args.target_version} 的下载文件已丢弃",
                  file=sys.stderr)
            return 1
        logger.info("sha256 校验通过: %s", args.target_version)
        if args.dry_run:
            print(f"✅ dry-run 通过: 下载 + sha256 校验 OK (target {args.target_version})")
            return 0
        backup_path = backup_exe(exe_path, args.current_version, Path(args.backup_dir))
        logger.info("已备份 %s -> %s", exe_path, backup_path)
        try:
            os.replace(tmp, exe_path)
        except OSError as exc:
            logger.error("原子替换失败: %s", exc)
            try:
                restore_backup(backup_path, exe_path)
                logger.info("已自动恢复备份 %s", backup_path)
            except OSError as rexc:
                logger.error("自动恢复备份失败: %s", rexc)
            print("❌ 替换失败,已尝试恢复备份。若 exe 仍异常,请关闭应用后运行 restore.bat 恢复。",
                  file=sys.stderr)
            return 1
        logger.info("原子替换完成: %s (%s)", exe_path, args.target_version)
        for old in plan_backup(args.backup_dir, keep=3):
            try:
                Path(old).unlink(missing_ok=True)
                logger.info("清理旧备份(LRU): %s", old)
            except OSError as exc:
                logger.warning("清理旧备份失败: %s (%s)", old, exc)
        print(f"✅ 已更新到 {args.target_version},健康检查 {args.health_check_seconds}s …")
        time.sleep(args.health_check_seconds)
        logger.info("健康检查通过(自身存活 %ss)", args.health_check_seconds)
        print("✅ 更新完成")
        return 0
    finally:
        tmp.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DSA 云端客户端更新器(子进程)")
    parser.add_argument("--current-version", required=True, help="当前版本(用于备份命名)")
    parser.add_argument("--target-version", required=True, help="目标版本")
    parser.add_argument("--url", required=True, help="新版本 exe 下载地址")
    parser.add_argument("--sha256", required=True, help="新版本 exe 的 sha256(十六进制)")
    parser.add_argument("--backup-dir", required=True, help="备份目录(LRU 保留 3 版)")
    parser.add_argument("--exe-path", default=None, help="待替换的主程序 exe 路径")
    parser.add_argument("--dry-run", action="store_true", help="仅下载 + 校验,不替换")
    parser.add_argument("--health-check-seconds", type=int, default=HEALTH_CHECK_SECONDS,
                        help="替换成功后存活检查秒数(默认 30)")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_console()
    args = build_parser().parse_args(argv)
    logger = _logger()
    logger.info("updater 启动: current=%s target=%s backup-dir=%s",
                args.current_version, args.target_version, args.backup_dir)
    try:
        return apply_update(args)
    except Exception as exc:  # noqa: BLE001
        logger.error("更新失败: %s", exc)
        print(f"❌ 更新失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())