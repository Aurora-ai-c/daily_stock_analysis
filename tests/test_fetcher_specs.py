# tests/test_fetcher_specs.py
import pytest
import tempfile
from pathlib import Path

from data_provider.specs import FetcherSpec, load_fetcher_specs, FetcherSpecValidationError


SAMPLE = """
fetchers:
  - name: akshare
    module: data_provider.akshare_fetcher
    fetcher_class: AkshareFetcher
    markets: [cn, hk]
    capabilities: [quote, bar, fundamental]
    priority: 1
    enabled: true
    rate_limit: 20
    timeout: 15
    env_required: []
    health_check: null
    version: "1"
"""


def load_fetcher_specs_from_text(text: str) -> list[FetcherSpec]:
    """把 YAML 文本写入临时文件后交给 load_fetcher_specs。"""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(text)
        tmp_path = Path(f.name)
    try:
        return load_fetcher_specs(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


class TestFetcherSpec:
    def test_parse_sample(self):
        specs = load_fetcher_specs_from_text(SAMPLE)
        assert len(specs) == 1
        s = specs[0]
        assert s.name == "akshare"
        assert s.capabilities == ["quote", "bar", "fundamental"]
        assert s.priority == 1

    def test_bad_capability_rejected(self):
        with pytest.raises(FetcherSpecValidationError):
            load_fetcher_specs_from_text(SAMPLE.replace("fundamental", "nonsense"))

    def test_missing_class_rejected(self):
        with pytest.raises(FetcherSpecValidationError):
            load_fetcher_specs_from_text(SAMPLE.replace("class: AkshareFetcher", "class: 123"))
