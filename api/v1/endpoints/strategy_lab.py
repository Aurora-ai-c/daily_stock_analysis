# -*- coding: utf-8 -*-
"""Strategy Lab endpoints: list / signal preview / backtest / evolve / publish."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.strategy_lab import (
    EvolveRequest,
    EvolveResponse,
    LabBacktestRequest,
    LabBacktestResponse,
    PublishRequest,
    PublishResponse,
    SignalPreviewRequest,
    SignalPreviewResponse,
    StrategyListItem,
    StrategyListResponse,
)
from src.services.strategy_lab_service import (
    list_strategies,
    preview_signals,
    publish_strategies,
    run_evolution,
    run_lab_backtest,
)

logger = logging.getLogger(__name__)

router = APIRouter()

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "strategy_signals"


@router.get(
    "/strategies",
    response_model=StrategyListResponse,
    responses={200: {"description": "策略列表"}, 500: {"description": "服务器错误", "model": ErrorResponse}},
    summary="获取策略列表",
)
def get_strategies() -> StrategyListResponse:
    try:
        items = [StrategyListItem(**item) for item in list_strategies(CONFIG_DIR)]
        return StrategyListResponse(items=items)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"获取策略列表失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"获取策略列表失败: {str(exc)}"},
        ) from exc


@router.post(
    "/signal-preview",
    response_model=SignalPreviewResponse,
    responses={200: {"description": "信号预览"}, 500: {"description": "服务器错误", "model": ErrorResponse}},
    summary="生成策略信号预览",
    description="复用每日信号 probe 机制，返回当前自选股×策略的信号 JSON",
)
async def signal_preview(request: SignalPreviewRequest) -> SignalPreviewResponse:
    try:
        result = await preview_signals(symbols=request.symbols, config_dir=CONFIG_DIR)
        return SignalPreviewResponse(**result)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"信号预览失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"信号预览失败: {str(exc)}"},
        ) from exc


@router.post(
    "/backtest",
    response_model=LabBacktestResponse,
    responses={200: {"description": "一键回测完成"}, 500: {"description": "服务器错误", "model": ErrorResponse}},
    summary="一键回测",
    description="对指定策略执行回测（自选股集会），返回总体指标与逐股信号",
)
async def lab_backtest(request: LabBacktestRequest) -> LabBacktestResponse:
    try:
        result = await run_lab_backtest(
            request.strategy_id,
            symbols=request.symbols,
            days=request.days,
            config_dir=CONFIG_DIR,
        )
        return LabBacktestResponse(**result)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_params", "message": str(exc)},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(f"一键回测失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"一键回测失败: {str(exc)}"},
        ) from exc


@router.post(
    "/evolve",
    response_model=EvolveResponse,
    responses={200: {"description": "进化完成"}, 500: {"description": "服务器错误", "model": ErrorResponse}},
    summary="LLM/参数搜索进化策略",
)
async def lab_evolve(request: EvolveRequest) -> EvolveResponse:
    try:
        result = await asyncio.to_thread(
            run_evolution,
            request.strategy_id,
            method=request.method,
            rounds=request.rounds,
            samples=request.samples,
            config_dir=CONFIG_DIR,
        )
        return EvolveResponse(**result)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_params", "message": str(exc)},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(f"策略进化失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"策略进化失败: {str(exc)}"},
        ) from exc


@router.post(
    "/publish",
    response_model=PublishResponse,
    responses={200: {"description": "发布完成"}, 500: {"description": "服务器错误", "model": ErrorResponse}},
    summary="发布策略到云端仓库",
    description="将选中的策略 YAML 提交并推送到云端 fork（复用现有 git 凭据）",
)
def lab_publish(request: PublishRequest) -> PublishResponse:
    try:
        result = publish_strategies(request.strategy_ids, repo_root=REPO_ROOT)
        return PublishResponse(items=result)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_params", "message": str(exc)},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(f"策略发布失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"策略发布失败: {str(exc)}"},
        ) from exc