# -*- coding: utf-8 -*-
"""IM webhook 直发:价格告警推送到钉钉/飞书/企业微信。

GitHub Secrets 只写不可读,因此客户端直发使用本地配置的 webhook
(DPAPI 加密存于 config.json),与仓库 Secrets 互相独立。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse

import requests

_TIMEOUT = 8


def _dingtalk_sign(secret: str, timestamp_ms: int) -> str:
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                      hashlib.sha256).digest()
    return urllib.parse.quote_plus(base64.b64encode(digest))


def _feishu_sign(secret: str, timestamp_s: int) -> str:
    string_to_sign = f"{timestamp_s}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def build_payload(channel: str, title: str, text: str,
                  url: str, secret: str) -> tuple[str, dict]:
    """返回 (final_url, json_payload)。不支持的平台抛 ValueError。"""
    ch = (channel or "").strip().lower()
    if ch == "dingtalk":
        final_url = url
        if secret:
            ts = int(time.time() * 1000)
            sep = "&" if "?" in url else "?"
            final_url = f"{url}{sep}timestamp={ts}&sign={_dingtalk_sign(secret, ts)}"
        return final_url, {"msgtype": "markdown",
                           "markdown": {"title": title, "text": f"## {title}\n\n{text}"}}
    if ch == "feishu":
        payload: dict = {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
        if secret:
            payload["timestamp"] = str(int(time.time()))
            payload["sign"] = _feishu_sign(secret, int(time.time()))
        return url, payload
    if ch == "wechat":
        return url, {"msgtype": "markdown", "markdown": {"content": f"**{title}**\n{text}"}}
    raise ValueError(f"不支持的 IM 渠道: {channel}(支持 dingtalk/feishu/wechat)")


def send_im(channel: str, url: str, secret: str, title: str, text: str,
            proxy: str | None = None, timeout: int = _TIMEOUT) -> tuple[bool, str]:
    """发送一条 IM 消息。返回 (ok, 错误信息或"")。不抛出。"""
    if not url:
        return False, "webhook URL 为空"
    try:
        final_url, payload = build_payload(channel, title, text, url, secret)
        proxies = {"http": proxy, "https": proxy} if proxy else None
        resp = requests.post(final_url, json=payload, timeout=timeout, proxies=proxies)
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        body = resp.json() if resp.content else {}
        if body.get("errcode") not in (None, 0):
            return False, f"errcode={body.get('errcode')}: {str(body.get('errmsg'))[:200]}"
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
