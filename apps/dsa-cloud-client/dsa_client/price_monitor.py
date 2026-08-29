# -*- coding: utf-8 -*-
"""价格监控引擎:本地轮询自选股报价,评估阈值规则并触发提醒。

规则来源(三类并存):
  1. 全局涨跌幅阈值(config.monitor_change_pct,相对昨收)
  2. 用户自定义价格上下限(每股 above/below,持久化)
  3. alphaevo 信号卡派生(止损位/目标位/接近买点)——把云端信号与本地监控打通

设计约束:监控线程内的任何单点失败(行情/解析/通知)都不允许拖垮循环;
触发按 rule_key 冷却(默认 30 分钟),条件持续满足时做周期性提醒而非刷屏。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from . import im_push, quotes as qmod
from .config import CONFIG_DIR

ALERTS_PATH = CONFIG_DIR / "price_monitor.json"
DEFAULT_COOLDOWN_SEC = 30 * 60
MAX_ALERTS = 200
NEAR_ENTRY_RATIO = 0.01


@dataclass
class Rule:
    key: str
    symbol: str
    kind: str            # above / below / pct_up / pct_down / stop_hit / target_hit / near_entry
    threshold: float | None
    label: str

    def to_dict(self) -> dict:
        return asdict(self)


def _rule_message(rule: Rule, price: float | None, change_pct: float | None) -> str:
    p = f"{price:.2f}" if price is not None else "N/A"
    if rule.kind == "above":
        return f"{rule.symbol} 现价 {p} 已突破上限 {rule.threshold:.2f}"
    if rule.kind == "below":
        return f"{rule.symbol} 现价 {p} 已跌破下限 {rule.threshold:.2f}"
    if rule.kind == "pct_up":
        return f"{rule.symbol} 涨幅 {change_pct:.2f}% 超过阈值 +{rule.threshold:.2f}%(现价 {p})"
    if rule.kind == "pct_down":
        return f"{rule.symbol} 跌幅 {change_pct:.2f}% 超过阈值 -{rule.threshold:.2f}%(现价 {p})"
    if rule.kind == "stop_hit":
        return f"{rule.symbol} 现价 {p} 已触及 alphaevo 止损位 {rule.threshold:.2f}"
    if rule.kind == "target_hit":
        return f"{rule.symbol} 现价 {p} 已达到 alphaevo 目标位 {rule.threshold:.2f}"
    if rule.kind == "near_entry":
        return f"{rule.symbol} 现价 {p} 接近 alphaevo 买点 {rule.threshold:.2f}(±1%)"
    return f"{rule.symbol} 触发规则 {rule.key}(现价 {p})"


def build_rules(symbols: list[str], change_pct_threshold: float,
                custom_rules: dict, signal_cards: list) -> list[Rule]:
    rules: list[Rule] = []
    for s in symbols:
        if change_pct_threshold > 0:
            rules.append(Rule(f"pct_up:{s}", s, "pct_up", change_pct_threshold,
                              f"涨幅超 {change_pct_threshold:g}%"))
            rules.append(Rule(f"pct_down:{s}", s, "pct_down", change_pct_threshold,
                              f"跌幅超 {change_pct_threshold:g}%"))
        cr = custom_rules.get(s) or {}
        above, below = cr.get("above"), cr.get("below")
        if isinstance(above, (int, float)) and above > 0:
            rules.append(Rule(f"above:{s}:{above:g}", s, "above", float(above),
                              f"突破 {above:g}"))
        if isinstance(below, (int, float)) and below > 0:
            rules.append(Rule(f"below:{s}:{below:g}", s, "below", float(below),
                              f"跌破 {below:g}"))
    for card in signal_cards or []:
        s = getattr(card, "symbol", None)
        if not s or s not in symbols:
            continue
        if getattr(card, "stop_loss", None):
            rules.append(Rule(f"stop_hit:{s}", s, "stop_hit", float(card.stop_loss),
                              f"alphaevo 止损 {card.stop_loss:g}"))
        if getattr(card, "target_price", None):
            rules.append(Rule(f"target_hit:{s}", s, "target_hit", float(card.target_price),
                              f"alphaevo 目标 {card.target_price:g}"))
        if getattr(card, "entry_price", None):
            rules.append(Rule(f"near_entry:{s}", s, "near_entry", float(card.entry_price),
                              f"alphaevo 买点 {card.entry_price:g}"))
    return rules


def evaluate(quote: "qmod.Quote", rules: list[Rule]) -> list[Rule]:
    """对单只报价评估规则,返回触发的规则列表。price 缺失时跳过。"""
    if quote is None or quote.price is None:
        return []
    hit: list[Rule] = []
    for r in rules:
        if r.symbol != quote.symbol:
            continue
        if r.kind == "above" and quote.price >= (r.threshold or 0):
            hit.append(r)
        elif r.kind == "below" and quote.price <= (r.threshold or 0):
            hit.append(r)
        elif r.kind == "pct_up" and (quote.change_pct or 0) >= (r.threshold or 0):
            hit.append(r)
        elif r.kind == "pct_down" and (quote.change_pct or 0) <= -(r.threshold or 0):
            hit.append(r)
        elif r.kind == "stop_hit" and quote.price <= (r.threshold or 0):
            hit.append(r)
        elif r.kind == "target_hit" and quote.price >= (r.threshold or 0):
            hit.append(r)
        elif r.kind == "near_entry" and r.threshold:
            if abs(quote.price - r.threshold) / r.threshold <= NEAR_ENTRY_RATIO:
                hit.append(r)
    return hit


def _load_store() -> dict:
    try:
        return json.loads(ALERTS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_store(store: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        ALERTS_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


class PriceMonitor:
    """单实例监控器:由 server 在应用创建时装配,start/stop 由 UI 驱动。"""

    def __init__(self, config, fetcher=None, notifier=None, sleep=time.sleep):
        self._config = config
        self._fetcher = fetcher or (lambda symbols: qmod.fetch_quotes(
            symbols, proxy=(config.github_proxy or "") or None))
        self._notifier = notifier          # callable(list[dict]) -> None
        self._sleep = sleep
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._signals: list = []           # 最新 alphaevo 信号卡
        self._watchlist: list[str] = []
        self.state: dict = {"quotes": {}, "alerts": [], "last_cycle_ts": 0.0,
                            "last_error": "", "cycles": 0, "watchlist": []}

    # ---- 生命周期 ----
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="dsa-price-monitor")
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def ensure_running(self, enabled: bool) -> None:
        if enabled:
            self.start()
        else:
            self.stop()

    # ---- 数据注入(server 侧调用) ----
    def set_watchlist(self, symbols: list[str]) -> None:
        with self._lock:
            self._watchlist = list(symbols)
            self.state["watchlist"] = list(symbols)

    def set_signals(self, cards: list) -> None:
        with self._lock:
            self._signals = list(cards or [])

    # ---- 规则管理(UI 驱动) ----
    def custom_rules(self) -> dict:
        return _load_store().get("custom_rules", {})

    def set_custom_rule(self, symbol: str, above: float | None, below: float | None) -> None:
        store = _load_store()
        rules = store.get("custom_rules", {})
        if above is None and below is None:
            rules.pop(symbol, None)
        else:
            rules[symbol] = {"above": above, "below": below}
        store["custom_rules"] = rules
        _save_store(store)

    # ---- 状态读取(UI 轮询) ----
    def snapshot(self) -> dict:
        with self._lock:
            snap = dict(self.state)
            snap["quotes"] = [q.to_dict() if hasattr(q, "to_dict") else q
                              for q in snap.get("quotes", {}).values()]
            snap["running"] = self.running
            snap["enabled"] = bool(self._config.monitor_enabled)
            snap["interval_sec"] = self._config.monitor_interval_sec
            snap["change_pct"] = self._config.monitor_change_pct
            snap["custom_rules"] = self.custom_rules()
            snap["signals_count"] = len(self._signals)
            snap["im_enabled"] = bool(self._config.notify_im_enabled)
            return snap

    def unread_count(self) -> int:
        return sum(1 for a in self.state.get("alerts", []) if not a.get("read"))

    def mark_alerts_read(self) -> None:
        with self._lock:
            for a in self.state.get("alerts", []):
                a["read"] = True
            store = _load_store()
            store["alerts"] = self.state["alerts"]
            _save_store(store)

    # ---- 主循环 ----
    def _loop(self) -> None:
        failures = 0
        while not self._stop_event.is_set():
            interval = max(15, int(self._config.monitor_interval_sec or 60))
            try:
                self._cycle()
                failures = 0
            except Exception as exc:  # noqa: BLE001
                failures += 1
                with self._lock:
                    self.state["last_error"] = f"{type(exc).__name__}: {exc}"
            # 连续失败时指数退避,最长 4 倍间隔
            sleep_s = interval * min(2 ** failures, 4) if failures else interval
            self._stop_event.wait(max(15, sleep_s))

    def _cycle(self) -> None:
        with self._lock:
            symbols = list(self._watchlist)
            cards = list(self._signals)
        if not symbols:
            return
        fetched = self._fetcher(symbols)
        now = time.time()
        store = _load_store()
        cooldowns = store.get("cooldowns", {})
        custom = store.get("custom_rules", {})
        rules = build_rules(symbols, self._config.monitor_change_pct, custom, cards)

        fresh_alerts: list[dict] = []
        for sym in symbols:
            q = fetched.get(sym) or qmod.Quote(symbol=sym, error="no_data", fetched_at=now)
            for rule in evaluate(q, rules):
                if now - cooldowns.get(rule.key, 0) < DEFAULT_COOLDOWN_SEC:
                    continue
                cooldowns[rule.key] = now
                fresh_alerts.append({
                    "ts": now,
                    "symbol": sym,
                    "kind": rule.kind,
                    "label": rule.label,
                    "price": q.price,
                    "change_pct": q.change_pct,
                    "message": _rule_message(rule, q.price, q.change_pct),
                    "read": False,
                })

        with self._lock:
            self.state["quotes"] = {s: fetched.get(s) for s in symbols}
            if fresh_alerts:
                self.state["alerts"] = (fresh_alerts + self.state.get("alerts", []))[:MAX_ALERTS]
            self.state["last_cycle_ts"] = now
            self.state["cycles"] += 1
            self.state["watchlist"] = list(symbols)

        if fresh_alerts:
            store["cooldowns"] = cooldowns
            store["alerts"] = self.state["alerts"]
            _save_store(store)
            self._notify(fresh_alerts)

    def _notify(self, alerts: list[dict]) -> None:
        text = "\n".join(f"• {a['message']}" for a in alerts)
        try:
            self._toast(text)
        except Exception:  # noqa: BLE001
            pass
        if self._notifier:
            try:
                self._notifier(alerts)
            except Exception:  # noqa: BLE001
                pass
        if self._config.notify_im_enabled:
            ok, err = im_push.send_im(
                self._config.im_webhook_type, self._config.get_im_webhook(),
                self._config.get_im_webhook_secret(),
                title="DSA 价格监控", text=text,
                proxy=(self._config.github_proxy or "") or None)
            if not ok:
                with self._lock:
                    self.state["last_error"] = f"IM 推送失败: {err}"

    @staticmethod
    def _toast(text: str) -> None:
        """Windows 系统通知;plyer 不可用时静默降级(界面角标仍会更新)。"""
        try:
            from plyer import notification
            notification.notify(title="DSA 价格监控", message=text, timeout=8)
        except Exception:  # noqa: BLE001
            pass
