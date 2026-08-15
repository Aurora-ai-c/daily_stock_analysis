#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DSA 云端客户端主入口:绑定 127.0.0.1 随机端口,打开浏览器。"""

from __future__ import annotations

import argparse
import logging
import random
import socket
import sys
import webbrowser
from pathlib import Path

import uvicorn

from dsa_client import config as cfg
from dsa_client.server import create_app

PORT_MIN, PORT_MAX = 49152, 65535


def _ensure_console() -> None:
    if sys.platform.startswith("win"):
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(errors="replace")
            except (AttributeError, ValueError):
                pass


def pick_port(retries: int = 3) -> int:
    last = None
    for _ in range(retries):
        port = random.randint(PORT_MIN, PORT_MAX)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError as e:
                last = e
                continue
    raise RuntimeError(f"Unable to bind any port in [{PORT_MIN},{PORT_MAX}]: {last}")


def main(argv=None) -> int:
    _ensure_console()
    parser = argparse.ArgumentParser("dsa-cloud-client")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = cfg.initialize_config()
    except Exception as e:  # noqa: BLE001
        print(f"❌ 初始化配置失败: {e}", file=sys.stderr)
        return 1

    log_file = cfg.CONFIG_DIR / "server.log"
    logging.basicConfig(
        filename=log_file, level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.info("DSA client starting; logged_in=%s", not config.validate())

    app = create_app(config, static_dir=Path(__file__).resolve().parent / "static")
    port = args.port or pick_port()
    url = f"http://127.0.0.1:{port}/#token={config.token}"

    print(f"\n🔒 DSA 云端客户端已启动\n   地址: {url}\n   日志: {log_file}\n")
    print("   即将自动打开浏览器;若未打开,请手动复制上方地址访问。")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            print("⚠️ 无法自动打开浏览器,请手动访问: " + url)

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
