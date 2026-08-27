# -*- coding: utf-8 -*-
"""
===================================
数据源基类与管理器
===================================

设计模式：策略模式 (Strategy Pattern)
- BaseFetcher: 抽象基类，定义统一接口
- DataFetcherManager: 策略管理器，实现自动切换

防封禁策略：
1. 每个 Fetcher 内置流控逻辑
2. 失败自动切换到下一个数据源
3. 指数退避重试机制
"""

import logging
import os
import random
import time
from threading import BoundedSemaphore, RLock, Thread
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Callable, Optional, List, Tuple, Dict, Any

import pandas as pd
import numpy as np
from src.data.stock_index_loader import get_index_stock_name
from src.data.stock_mapping import STOCK_NAME_MAP, is_meaningful_stock_name
from src.services.market_symbol_utils import is_suffix_market_symbol
from src.services.run_diagnostics import record_provider_run, record_provider_run_started
from ..fundamental_adapter import AkshareFundamentalAdapter
from ..yfinance_fundamental_adapter import YfinanceFundamentalAdapter
from ..realtime_types import CircuitBreaker, UnifiedRealtimeQuote
from ..specs import FetcherSpec

import requests
import tenacity

# 配置日志
logger = logging.getLogger(__name__)

# === 请求级瞬态错误重试（瞬态网络错误，非持久错误） ===
# 覆盖今晚出现的 SSL(CERTIFICATE_VERIFY_FAILED)/RemoteDisconnected 等瞬态,
# 在单源内部重试若干次,失败再交由上层多源 fallback 切到下一源。
_TRANSIENT_NETWORK_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.SSLError,
)
class DataFetchError(Exception):
    """数据获取异常基类"""
    pass


class RateLimitError(DataFetchError):
    """API 速率限制异常"""
    pass


class DataSourceUnavailableError(DataFetchError):
    """数据源不可用异常"""
    pass

__all__ = ['logger', '_TRANSIENT_NETWORK_ERRORS', 'DataFetchError', 'RateLimitError', 'DataSourceUnavailableError']
