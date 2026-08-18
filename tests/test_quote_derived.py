# tests/test_quote_derived.py
import pytest
from datetime import date, timedelta
from data_provider.contracts import Bar, Quote, FundamentalRaw
from data_provider.quote_derived import QuoteDerivedCalculator


def _bars(days: int, base_vol: int = 1000):
    out = []
    for i in range(days):
        d = date(2026, 8, 1) + timedelta(days=i)
        out.append(Bar(date=d, open=10, high=11, low=9, close=10, volume=base_vol))
    return out


def test_volume_ratio_uses_prev_5d_avg():
    bars = _bars(6)
    bars[-1] = Bar(date=bars[-1].date, open=10, high=11, low=9, close=10, volume=3000)
    q = Quote(code="600519", price=10.0, volume=3000, pre_close=9.5, high=11.0, low=9.0)
    d = QuoteDerivedCalculator().calculate(q, bars=bars)
    assert d.volume_ratio == pytest.approx(3.0, abs=0.01)


def test_amplitude_from_pre_close():
    q = Quote(code="600519", price=10.0, pre_close=9.5, high=11.0, low=9.0)
    d = QuoteDerivedCalculator().calculate(q)
    assert d.amplitude == pytest.approx(21.05, abs=0.01)


def test_no_bars_means_none():
    q = Quote(code="600519", price=10.0)
    d = QuoteDerivedCalculator().calculate(q)
    assert d.volume_ratio is None


def test_pre_close_zero_gives_none():
    q = Quote(code="600519", price=10.0, pre_close=0, high=11.0, low=9.0)
    d = QuoteDerivedCalculator().calculate(q)
    assert d.amplitude is None


def test_insufficient_bars_gives_none():
    q = Quote(code="600519", price=10.0, volume=3000)
    d = QuoteDerivedCalculator().calculate(q, bars=_bars(4))
    assert d.volume_ratio is None


def test_zero_volume_avg_gives_none():
    q = Quote(code="600519", price=10.0, volume=3000)
    d = QuoteDerivedCalculator().calculate(q, bars=_bars(6, base_vol=0))
    assert d.volume_ratio is None