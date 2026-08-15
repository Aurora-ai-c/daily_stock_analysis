# -*- coding: utf-8 -*-
"""FastAPI 本地服务:绑定 127.0.0.1,token + Origin 校验,静态页面挂载。"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import github_client as gc, signals as sig, config as cfg_mod

CONFIG_DIR = cfg_mod.CONFIG_DIR


class WatchlistBody(BaseModel):
    symbols: str


class TriggerBody(BaseModel):
    mode: str = "full"
    stock_list: str | None = None


def _check_token(config, token: str) -> bool:
    return token == config.token


def _check_origin(request, config) -> bool:
    return request.headers.get("X-Origin-Token") == config.token


def create_app(config: "cfg_mod.Config", static_dir: Path | None = None,
               client_factory=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    git_factory = client_factory or (lambda c: gc.GitHubClient(c.get_pat()))

    if static_dir is not None and static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.middleware("http")
    async def _headers_only(request, call_next):
        response = await call_next(request)
        response.headers["cache-control"] = "no-store"
        response.headers["x-content-type-options"] = "nosniff"
        return response

    @app.get("/health")
    def health():
        return {"status": "ok"}

    def _guard(request):
        return _check_token(config, request.query_params.get("token"))

    def open_index(static_dir):
        if static_dir is not None and (static_dir / "index.html").exists():
            return FileResponse(static_dir / "index.html")
        return JSONResponse({"hint": "static/index.html 由前端任务(C2)提供"}, status_code=200)

    @app.get("/")
    def index(request: Request):
        return open_index(static_dir)

    @app.get("/api/state")
    def state(request: Request):
        if not _guard(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        git = git_factory(config)
        logged_in = bool(config.owner and config.repo)
        running = False
        if logged_in:
            try:
                running = gc.is_running(git.get_runs(config.owner, config.repo))
            except Exception:
                running = False
        return {"owner": config.owner, "repo": config.repo, "logged_in": logged_in, "running": running}

    @app.get("/api/watchlist")
    def watchlist(request: Request):
        if not _guard(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        git = git_factory(config)
        return {"symbols": git.get_variable(config.owner, config.repo, "STOCK_LIST")}

    @app.patch("/api/watchlist")
    def watchlist_update(request: Request, body: WatchlistBody):
        if not (_guard(request) and _check_origin(request, config)):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        git = git_factory(config)
        git.set_variable(config.owner, config.repo, "STOCK_LIST", body.symbols)
        return {"ok": True}

    @app.post("/api/trigger")
    def trigger(request: Request, body: TriggerBody):
        if not (_guard(request) and _check_origin(request, config)):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        git = git_factory(config)
        inputs = {"mode": body.mode}
        if body.stock_list:
            inputs["stock_list"] = body.stock_list
        git.dispatch(config.owner, config.repo, ref="main", inputs=inputs)
        return {"ok": True}

    @app.get("/api/reports")
    def reports(request: Request):
        if not _guard(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        git = git_factory(config)
        return {"reports": git.list_artifacts(config.owner, config.repo)}

    @app.get("/api/reports/{artifact_id}/download")
    def report_download(request: Request, artifact_id: int):
        if not _guard(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        git = git_factory(config)
        data = git.download_artifact(config.owner, config.repo, artifact_id)
        archive_dir = CONFIG_DIR / "archive" / config.repo
        archive_dir.mkdir(parents=True, exist_ok=True)
        target = archive_dir / f"{artifact_id}.zip"
        target.write_bytes(data)
        return {"ok": True, "path": str(target)}

    return app


def run_server(app, port: int, log_file) -> None:
    import uvicorn
    from uvicorn.config import LOGGING_CONFIG

    log_config = json.loads(json.dumps(LOGGING_CONFIG))
    log_config["handlers"]["file"] = {"class": "logging.FileHandler", "filename": str(log_file)}
    for logger_name, logger_cfg in log_config["loggers"].items():
        handlers = list(logger_cfg.get("handlers", [])) + ["file"]
        logger_cfg["handlers"] = handlers
        logger_cfg.pop("propagate", None)
    uvicorn.run(app, host="127.0.0.1", port=port, log_config=log_config)
