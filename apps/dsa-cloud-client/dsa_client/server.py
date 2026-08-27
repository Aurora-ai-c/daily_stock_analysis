# -*- coding: utf-8 -*-
"""FastAPI 本地服务:绑定 127.0.0.1,token + Origin 校验,静态页面挂载。"""

from __future__ import annotations

import hmac
import io
import json
import zipfile
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import github_client as gc, config as cfg_mod, state_store as ss

CONFIG_DIR = cfg_mod.CONFIG_DIR


def fetch_data_source_health(config) -> dict | None:
    """从最新 analysis-reports 工件解包 data_source_health.json(云端导出)。

    返回健康 dict 或 None(无凭据/无工件/解析失败)。
    """
    if not (config.owner and config.repo and config.get_pat()):
        return None
    try:
        git = gc.GitHubClient(
            token=config.get_pat(),
            proxy=getattr(config, "github_proxy", "") or None,
            ca_bundle=getattr(config, "github_ca_bundle", "") or None,
        )
        artifacts = git.list_artifacts(config.owner, config.repo, per_page=20)
        candidates = [a for a in artifacts if a.get("name", "").startswith("analysis-reports-")]
        candidates.sort(key=lambda a: a.get("name", ""), reverse=True)
        for art in candidates:
            if art.get("expired"):
                continue
            try:
                data = git.download_artifact(config.owner, config.repo, int(art["id"]))
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    if "data_source_health.json" in zf.namelist():
                        return json.loads(zf.read("data_source_health.json").decode("utf-8"))
            except Exception:
                continue
    except Exception:
        return None
    return None


class WatchlistBody(BaseModel):
    symbols: str


class TriggerBody(BaseModel):
    mode: str = "full"
    stock_list: str | None = None


class LoginBody(BaseModel):
    owner: str
    repo: str
    pat: str


class BudgetBody(BaseModel):
    budget_daily_usd: float = 1.0
    budget_mode: str = "warn"


class SecretBody(BaseModel):
    name: str
    value: str


def _check_token(config, token) -> bool:
    if not isinstance(token, str) or not isinstance(config.token, str):
        return False
    return hmac.compare_digest(token, config.token)


def _check_origin(request, config) -> bool:
    return request.headers.get("X-Origin-Token") == config.token


def create_app(config: "cfg_mod.Config", static_dir: Path | None = None,
               client_factory=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def _default_factory(c: cfg_mod.Config) -> gc.GitHubClient:
        return gc.GitHubClient(
            c.get_pat(),
            proxy=c.github_proxy or None,
            ca_bundle=c.github_ca_bundle or None,
        )

    git_factory = client_factory or _default_factory


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
        pat = config.get_pat()
        logged_in = bool(config.owner and config.repo and pat)
        running = False
        if logged_in:
            try:
                git = git_factory(config)
                running = gc.is_running(git.get_runs(config.owner, config.repo))
            except Exception:
                running = False
        return {"owner": config.owner, "repo": config.repo, "logged_in": logged_in,
                "running": running,
                "pat_configured": bool(pat),
                "needs_login": not (config.owner and config.repo and pat)}

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
        # 成本护栏:触发前预估并核对当日预算
        if body.stock_list:
            num = len([s for s in body.stock_list.replace("，", ",").split(",") if s.strip()])
        else:
            wl = git_factory(config).get_variable(config.owner, config.repo, "STOCK_LIST") or ""
            num = len([s for s in wl.replace("，", ",").split(",") if s.strip()]) or 1
        est = ss.estimate_cost(num)
        spent = ss.today_spend()
        projected = spent + est
        if config.budget_mode == "block" and projected > config.budget_daily_usd:
            return JSONResponse({
                "error": "budget_exceeded",
                "message": f"预估 ${est:.2f} + 已花 ${spent:.2f} = ${projected:.2f} "
                           f"超过日预算 ${config.budget_daily_usd:.2f}(block 模式已拦截)",
            }, status_code=429)
        git = git_factory(config)
        inputs = {"mode": body.mode}
        if body.stock_list:
            inputs["stock_list"] = body.stock_list
        git.dispatch(config.owner, config.repo, ref="main", inputs=inputs)
        ss.add_spend(est)
        return {"ok": True, "estimated_usd": est, "projected_today_usd": ss.today_spend()}

    @app.get("/api/status")
    def status(request: Request):
        if not _guard(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        state = ss.load_run_state()
        running = False
        if config.owner and config.repo and config.get_pat():
            try:
                git = git_factory(config)
                runs = git.get_runs(config.owner, config.repo, limit=5)
                for r in runs:
                    ss.record_run_outcome(state, r)
                ss.save_run_state(state)
                running = gc.is_running(runs)
            except Exception:
                pass
        data_source_health = None
        if config.owner and config.repo and config.get_pat():
            try:
                data_source_health = fetch_data_source_health(config)
            except Exception:
                data_source_health = None
        return {
            "last_success_ts": state.get("last_success_ts", 0),
            "last_failure_ts": state.get("last_failure_ts", 0),
            "last_checked_ts": state.get("last_checked_ts", 0),
            "stale": ss.is_stale(state),
            "running": running,
            "today_spend_usd": ss.today_spend(),
            "budget_daily_usd": config.budget_daily_usd,
            "budget_mode": config.budget_mode,
            "data_source_health": data_source_health,
        }

    @app.get("/api/budget")
    def budget_get(request: Request):
        if not _guard(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return {
            "budget_daily_usd": config.budget_daily_usd,
            "budget_mode": config.budget_mode,
            "today_spend_usd": ss.today_spend(),
        }

    @app.post("/api/budget")
    def budget_set(request: Request, body: BudgetBody):
        if not (_guard(request) and _check_origin(request, config)):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        config.budget_daily_usd = max(0.0, float(body.budget_daily_usd))
        config.budget_mode = body.budget_mode if body.budget_mode in ("warn", "block") else "warn"
        config.save()
        return {"ok": True, "budget_daily_usd": config.budget_daily_usd, "budget_mode": config.budget_mode}

    @app.post("/api/network")
    def network_set(request: Request, body: dict):
        if not (_guard(request) and _check_origin(request, config)):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        config.github_proxy = (body.get("github_proxy") or "").strip()
        config.github_ca_bundle = (body.get("github_ca_bundle") or "").strip()
        config.save()
        return {"ok": True}

    @app.get("/api/secrets")
    def secrets_list(request: Request):
        if not _guard(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if not (config.owner and config.repo and config.get_pat()):
            return {"names": []}
        try:
            git = git_factory(config)
            names = git.list_secret_names(config.owner, config.repo)
        except Exception:
            names = []
        return {"names": names}

    @app.post("/api/secrets")
    def secrets_set(request: Request, body: SecretBody):
        if not (_guard(request) and _check_origin(request, config)):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        name = (body.name or "").strip()
        if not name or not body.value:
            return JSONResponse({"error": "invalid", "message": "name 与 value 均不能为空"}, status_code=400)
        try:
            git = git_factory(config)
            git.set_secret(config.owner, config.repo, name, body.value)
        except Exception as exc:
            return JSONResponse({"error": "set_failed", "message": str(exc)}, status_code=502)
        return {"ok": True, "name": name}

    @app.post("/api/login")
    def api_login(request: Request, body: LoginBody):
        if not (_guard(request) and _check_origin(request, config)):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        config.owner = body.owner
        config.repo = body.repo
        config.set_pat(body.pat)
        config.save()
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
