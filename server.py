# -*- coding: utf-8 -*-
"""
===================================
Daily Stock Analysis - FastAPI 后端服务入口
===================================

职责：
1. 提供 RESTful API 服务
2. 配置 CORS 跨域支持
3. 健康检查接口
4. 托管前端静态文件（生产模式）

启动方式：
    python server.py                 # 直接运行，绑定 WEBUI_HOST/WEBUI_PORT（默认 127.0.0.1:8000）

    或使用 main.py:
    python main.py --serve-only      # 仅启动 API 服务
    python main.py --serve           # API 服务 + 执行分析

    或使用 uvicorn CLI（此时绑定地址由 CLI 参数决定）:
    uvicorn server:app --reload --port 8000
"""

import logging

from src.config import setup_env, get_config
from src.logging_config import setup_logging

# 初始化环境变量与日志
setup_env()

config = get_config()
level_name = (config.log_level or "INFO").upper()
level = getattr(logging, level_name, logging.INFO)

setup_logging(
    log_prefix="api_server",
    console_level=level,
    extra_quiet_loggers=['uvicorn', 'fastapi'],
)

# 从 api.app 导入应用实例
from api.app import app  # noqa: E402

# 导出 app 供 uvicorn 使用
__all__ = ['app']


if __name__ == "__main__":
    import uvicorn

    from src.auth import is_auth_enabled

    # 与 main.py 的 _resolve_web_service_bind 对齐：默认仅绑定本机回环地址，
    # 避免无认证 API 意外暴露到局域网/公网；需要对外时显式设置 WEBUI_HOST。
    host = config.webui_host or "127.0.0.1"
    port = config.webui_port or 8000
    if (host or "").strip().lower() in {"0.0.0.0", "::", "[::]", "*"} and not is_auth_enabled():
        logging.getLogger(__name__).warning(
            "WEBUI_HOST=%s binds the Web UI to a public interface while "
            "ADMIN_AUTH_ENABLED=false. Keep this service behind a trusted network "
            "boundary or enable admin authentication before exposing it.",
            host,
        )

    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        reload=True,
    )
