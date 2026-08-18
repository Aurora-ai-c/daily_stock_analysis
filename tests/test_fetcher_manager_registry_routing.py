# -*- coding: utf-8 -*-
"""DataFetcherManager 注册表路由测试(CONNECTOR_V2_ENABLED flag 切换)。"""
import pytest

from data_provider.base import DataFetcherManager
from data_provider.specs import FetcherSpec


class _SpecFetcher:
    name = "spec_quote"
    priority = 5

    def __init__(self):
        self.calls = []

    def get_realtime_quote(self, code, **kw):
        self.calls.append(code)
        from data_provider.contracts import Quote
        return Quote(code=code, price=10.0).legacy_compat()


class TestRegistryRouting:
    def test_flag_on_routes_by_spec(self, monkeypatch):
        fake = _SpecFetcher()
        manager = DataFetcherManager(fetchers=[fake])
        spec = FetcherSpec(name="spec_quote", module="x", fetcher_class="y",
                           markets=["cn"], capabilities=["quote"], enabled=True)
        monkeypatch.setattr(manager, "registry_specs", {"spec_quote": spec})
        monkeypatch.setattr(manager, "_spec_instance", lambda spec: fake)
        monkeypatch.setattr("src.config.get_config", lambda: type("C", (), {"connector_v2_enabled": True})())
        quote = manager.get_realtime_quote("600519")
        assert quote is not None and fake.calls == ["600519"]

    def test_flag_off_keeps_legacy_path(self, monkeypatch):
        # flag 关时走旧 if/else 路径:空 fetchers + 空 source_priority → None,且不触注册表
        manager = DataFetcherManager(fetchers=[])
        monkeypatch.setattr(
            "src.config.get_config",
            lambda: type("C", (), {
                "connector_v2_enabled": False,
                "enable_realtime_quote": True,
                "realtime_source_priority": "",
            })(),
        )
        registry_calls = []
        monkeypatch.setattr(
            "data_provider.registry.discover_fetchers",
            lambda *args, **kwargs: registry_calls.append(1) or [],
        )
        quote = manager.get_realtime_quote("999999")
        assert quote is None
        assert registry_calls == []
