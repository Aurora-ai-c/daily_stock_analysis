# -*- coding: utf-8 -*-
"""
FinnhubFetcher — US market data source (Priority 2)

Data source: Finnhub.io REST API
Rate limit: 60 calls/min (free tier)
Markets: US only
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

import pandas as pd
import requests

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource
from .us_index_mapping import is_us_stock_code

logger = logging.getLogger(__name__)

_FINNHUB_BASE_URL = "https://finnhub.io/api/v1"


class FinnhubFetcher(BaseFetcher):
    name = "FinnhubFetcher"
    priority = 2

    def __init__(self):
        from src.config import get_config
        config = get_config()
        self._api_key = getattr(config, 'finnhub_api_key', None) or os.getenv('FINNHUB_API_KEY')
        if not self._api_key:
            logger.debug("[Finnhub] API key not configured, fetcher disabled")

    def _is_us_stock(self, stock_code: str) -> bool:
        return is_us_stock_code(stock_code)

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        if not self._api_key:
            raise DataFetchError("[Finnhub] API key not configured")
        if not self._is_us_stock(stock_code):
            raise DataFetchError(f"[Finnhub] {stock_code} is not a US stock")

        symbol = stock_code.strip().upper()
        start_ts = int(datetime.strptime(start_date, '%Y-%m-%d').timestamp())
        end_ts = int(datetime.strptime(end_date, '%Y-%m-%d').timestamp())

        url = f"{_FINNHUB_BASE_URL}/stock/candle"
        params = {
            'symbol': symbol,
            'resolution': 'D',
            'from': start_ts,
            'to': end_ts,
            'token': self._api_key,
        }

        try:
            self.random_sleep(0.3, 0.8)
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise DataFetchError(f"[Finnhub] HTTP request failed for {symbol}: {e}") from e

        if data.get('s') != 'ok' or not data.get('c'):
            raise DataFetchError(f"[Finnhub] No data returned for {symbol}")

        return pd.DataFrame({
            'c': data['c'],
            'h': data['h'],
            'l': data['l'],
            'o': data['o'],
            't': data['t'],
            'v': data['v'],
        })

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        if df.empty:
            return df

        df = df.copy()
        df['date'] = pd.to_datetime(df['t'], unit='s').dt.date
        df = df.rename(columns={
            'o': 'open', 'h': 'high', 'l': 'low',
            'c': 'close', 'v': 'volume',
        })
        df['pct_chg'] = df['close'].pct_change() * 100
        df['pct_chg'] = df['pct_chg'].fillna(0).round(2)
        df['amount'] = df['volume'] * df['close']
        df['code'] = stock_code

        keep = ['code'] + STANDARD_COLUMNS
        df = df[[col for col in keep if col in df.columns]]
        return df

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        if not self._api_key or not self._is_us_stock(stock_code):
            return None

        symbol = stock_code.strip().upper()
        try:
            self.random_sleep(0.3, 0.8)
            resp = requests.get(
                f"{_FINNHUB_BASE_URL}/quote",
                params={'symbol': symbol, 'token': self._api_key},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[Finnhub] Realtime quote failed for {symbol}: {e}")
            return None

        price = data.get('c')
        if not price:
            return None

        prev_close = data.get('pc', 0)
        change_pct = data.get('dp')
        change_amount = data.get('d')
        high = data.get('h')
        low = data.get('l')
        open_price = data.get('o')

        amplitude = None
        if high and low and prev_close and prev_close > 0:
            amplitude = round((high - low) / prev_close * 100, 2)

        return UnifiedRealtimeQuote(
            code=symbol,
            source=RealtimeSource.FALLBACK,
            price=price,
            change_pct=round(change_pct, 2) if change_pct is not None else None,
            change_amount=round(change_amount, 4) if change_amount is not None else None,
            volume=data.get('v'),
            amount=None,
            volume_ratio=None,
            turnover_rate=None,
            amplitude=amplitude,
            open_price=open_price,
            high=high,
            low=low,
            pre_close=prev_close,
        )

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        if not self._api_key or not self._is_us_stock(stock_code):
            return None

        symbol = stock_code.strip().upper()
        try:
            resp = requests.get(
                f"{_FINNHUB_BASE_URL}/search",
                params={'q': symbol, 'token': self._api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.debug(f"[Finnhub] Symbol search failed for {symbol}: {e}")
            return None

        for item in data.get('result', []):
            if item.get('symbol') == symbol and item.get('description'):
                return item['description']
        return None

    def to_quote(self, raw: UnifiedRealtimeQuote) -> Quote:
        """旧 UnifiedRealtimeQuote → 新 Quote(缺失字段容忍)。"""
        from data_provider.contracts import Quote
        return Quote(
            code=raw.code, name=raw.name,
            price=raw.price, open_price=raw.open_price, high=raw.high, low=raw.low,
            pre_close=raw.pre_close, volume=raw.volume, amount=raw.amount,
            change_pct=raw.change_pct, change_amount=raw.change_amount,
            tz="America/New_York", currency=raw.currency, market=raw.market,
            fetched_at=raw.fetched_at, provider_timestamp=raw.provider_timestamp,
            is_stale=raw.is_stale, stale_seconds=raw.stale_seconds,
            fallback_from=raw.fallback_from, data_quality=raw.data_quality,
            missing_fields=raw.missing_fields,
        )

    def to_bar(self, df: pd.DataFrame) -> list[Bar]:
        """标准日线 DataFrame → list[Bar]。"""
        from data_provider.contracts import Bar
        bars = []
        for _, row in df.iterrows():
            try:
                bars.append(Bar(
                    date=pd.to_datetime(row["date"]).date(),
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=int(row["volume"]),
                    amount=float(row["amount"]) if pd.notna(row.get("amount")) else None,
                    pct_chg=float(row["pct_chg"]) if pd.notna(row.get("pct_chg")) else None,
                ))
            except (KeyError, ValueError, TypeError):
                continue
        return bars

    def to_fundamental(self, payload: dict) -> Optional[FundamentalRaw]:
        """finnhub 财报 dict → FundamentalRaw;缺 report_date 返回 None。"""
        from data_provider.contracts import FundamentalRaw
        report_date = payload.get("report_date")
        if not report_date:
            return None
        return FundamentalRaw(
            report_date=pd.to_datetime(report_date).date(),
            fiscal_period=str(payload.get("fiscal_period") or "FY"),
            market=str(payload.get("market") or "us"),
            total_assets=payload.get("total_assets"),
            total_liabilities=payload.get("total_liabilities"),
            total_equity=payload.get("total_equity"),
            revenue=payload.get("revenue"),
            net_income=payload.get("net_income"),
            operating_cashflow=payload.get("operating_cashflow"),
            investing_cashflow=payload.get("investing_cashflow"),
            financing_cashflow=payload.get("financing_cashflow"),
            gross_margin=payload.get("gross_margin"),
            dividend_yield=payload.get("dividend_yield"),
            industry=payload.get("industry"),
        )
