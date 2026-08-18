# tests/test_fetcher_registry.py
import pytest
from data_provider.registry import discover_fetchers, FetcherRegistryError
from data_provider.specs import FetcherSpec, load_fetcher_specs


class TestDiscover:
    def test_class_not_found_fails_fast(self, monkeypatch):
        specs = [FetcherSpec(name="bad", module="data_provider.contracts", fetcher_class="NoSuchClass",
                             capabilities=["quote"], enabled=True)]
        monkeypatch.setattr("data_provider.registry.load_fetcher_specs", lambda path: specs)
        with pytest.raises(FetcherRegistryError):
            discover_fetchers()

    def test_module_not_found_fails_fast(self, monkeypatch):
        specs = [FetcherSpec(name="bad", module="no.such.module", fetcher_class="X",
                             capabilities=["quote"], enabled=True)]
        monkeypatch.setattr("data_provider.registry.load_fetcher_specs", lambda path: specs)
        with pytest.raises(FetcherRegistryError):
            discover_fetchers()

    def test_missing_env_disables_warn_only(self, monkeypatch):
        specs = [FetcherSpec(name="t", module="data_provider.contracts", fetcher_class="Quote",
                             capabilities=["quote"], enabled=True,
                             env_required=["DEFINITELY_NOT_SET_VAR_XYZ"])]
        monkeypatch.setattr("data_provider.registry.load_fetcher_specs", lambda path: specs)
        monkeypatch.delenv("DEFINITELY_NOT_SET_VAR_XYZ", raising=False)
        out = discover_fetchers()
        assert out[0].enabled is False

    def test_health_check_false_disables(self, monkeypatch):
        specs = [FetcherSpec(name="h", module="data_provider.contracts", fetcher_class="Quote",
                             capabilities=["quote"], enabled=True,
                             health_check="tests.test_fetcher_registry:_health_false")]
        monkeypatch.setattr("data_provider.registry.load_fetcher_specs", lambda path: specs)
        out = discover_fetchers()
        assert out[0].enabled is False

    def test_valid_spec_passes(self, monkeypatch):
        specs = [FetcherSpec(name="ok", module="data_provider.contracts", fetcher_class="Quote",
                             capabilities=["quote"], enabled=True)]
        monkeypatch.setattr("data_provider.registry.load_fetcher_specs", lambda path: specs)
        out = discover_fetchers()
        assert out[0].enabled is True


def _health_false() -> bool:
    return False
