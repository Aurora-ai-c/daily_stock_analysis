# -*- coding: utf-8 -*-
"""轻量行情拉取:统一走腾讯免费行情接口(A 股/港股/美股),仅依赖 requests。

无 SLA 公共接口:调用方负责低频轮询与失败退避;单只失败返回 price=None
的 Quote,不抛出 —— 单源失败不能拖垮监控线程。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, asdict

import requests

_UA = {"User-Agent": "Mozilla/5.0 (dsa-cloud-client price monitor)"}
_TENCENT_URL = "https://qt.gtimg.cn/q={codes}"
_TIMEOUT = 10


@dataclass
class Quote:
    symbol: str                      # 调用方传入的原始代码
    name: str = ""
    price: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None
    source: str = ""
    fetched_at: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def market_of(symbol: str) -> str:
    """us / hk / cn;无法识别按 cn 处理(与仓库既有约定一致:6 位 A 股、4-5 位港股)。"""
    s = (symbol or "").strip()
    if s.upper().startswith("US."):
        return "us"
    low = s.lower()
    if low.startswith("hk"):
        return "hk"
    if low.startswith(("sh", "sz", "bj")):
        return "cn"
    if re.fullmatch(r"\d{4,5}", s):
        return "hk"
    return "cn"


def _tencent_code(symbol: str) -> str | None:
    """归一代码 → 腾讯行情代码。美股腾讯可自动识别交易所,无需 .OQ/.N 后缀。"""
    s = symbol.strip()
    if s.upper().startswith("US."):
        base = s[3:].strip().upper()
        return f"us{base}" if re.fullmatch(r"[A-Za-z.\-]{1,10}", base) else None
    if re.fullmatch(r"\d{6}", s):
        return ("sh" if s.startswith("6") else "sz") + s
    m = re.fullmatch(r"(?i)(sh|sz|bj)(\d{6})", s)
    if m:
        return m.group(1).lower() + m.group(2)
    m = re.fullmatch(r"(?i)hk(\d{4,5})", s)
    if m:
        return "hk" + m.group(1).zfill(5)
    if re.fullmatch(r"\d{4,5}", s):
        return "hk" + s.zfill(5)
    return None


def _to_float(v: str) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def parse_tencent_response(body: str, requested: dict[str, str]) -> dict[str, Quote]:
    """解析 qt.gtimg.cn 文本(GBK 解码后)。requested: {tencent_code: 原始symbol}。"""
    out: dict[str, Quote] = {}
    for line in body.splitlines():
        m = re.match(r'v_(\w+)="([^"]*)"', line.strip())
        if not m:
            continue
        code, payload = m.group(1), m.group(2)
        symbol = requested.get(code)
        if not symbol:
            continue
        fields = payload.split("~")
        quote = Quote(symbol=symbol, source="tencent", fetched_at=time.time())
        if len(fields) > 4:
            quote.name = fields[1]
            quote.price = _to_float(fields[3])
            quote.prev_close = _to_float(fields[4])
            if quote.price is not None and quote.prev_close:
                quote.change_pct = round((quote.price / quote.prev_close - 1) * 100, 2)
        if quote.price is None:
            quote.error = "tencent_empty"
        out[symbol] = quote
    return out


def fetch_quotes(symbols: list[str], proxy: str | None = None,
                 timeout: int = _TIMEOUT) -> dict[str, Quote]:
    """拉取一组代码的报价。返回 {symbol: Quote};失败的 symbol 也有条目(price=None)。"""
    result: dict[str, Quote] = {s: Quote(symbol=s, fetched_at=time.time()) for s in symbols}
    requested: dict[str, str] = {}
    for s in symbols:
        code = _tencent_code(s)
        if code:
            requested[code] = s
        else:
            result[s] = Quote(symbol=s, fetched_at=time.time(), error="unrecognized_symbol")
    if not requested:
        return result
    url = _TENCENT_URL.format(codes=",".join(requested))
    try:
        resp = requests.get(url, headers=_UA, timeout=timeout,
                            proxies={"http": proxy, "https": proxy} if proxy else None)
        resp.raise_for_status()
        result.update(parse_tencent_response(resp.content.decode("gbk", errors="replace"), requested))
    except Exception as exc:  # noqa: BLE001
        for s in requested.values():
            result[s] = Quote(symbol=s, fetched_at=time.time(),
                              error=f"{type(exc).__name__}: {exc}")
    return result
