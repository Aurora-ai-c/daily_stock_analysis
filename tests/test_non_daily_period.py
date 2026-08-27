"""Phase 1.3: weekly/monthly K-line support (daily fetch + resample)."""

import datetime
import unittest
from unittest import mock

import pandas as pd

from data_provider.base import DataFetcherManager
from src.services.stock_service import StockService


def _daily_df(n: int = 30) -> pd.DataFrame:
    end = datetime.date(2024, 1, 31)
    dates = [end - datetime.timedelta(days=i) for i in range(n)][::-1]
    return pd.DataFrame(
        {
            "date": [d.isoformat() for d in dates],
            "open": [10.0 + i for i in range(n)],
            "high": [11.0 + i for i in range(n)],
            "low": [9.0 + i for i in range(n)],
            "close": [10.5 + i for i in range(n)],
            "volume": [1000 + i for i in range(n)],
            "amount": [10000 + i for i in range(n)],
            "pct_chg": [1.0 for _ in range(n)],
        }
    )


class TestResamplePeriod(unittest.TestCase):
    def setUp(self):
        self.mgr = DataFetcherManager()

    def test_daily_passthrough(self):
        df = _daily_df(10)
        out = self.mgr._resample_to_period(df, "daily")
        self.assertEqual(len(out), 10)

    def test_resample_weekly_fewer_rows(self):
        df = _daily_df(30)
        wk = self.mgr._resample_to_period(df, "weekly")
        self.assertIn("date", wk.columns)
        self.assertLess(len(wk), len(df))
        self.assertGreater(len(wk), 0)
        # 30 calendar days -> about 5 weekly bars
        self.assertLessEqual(len(wk), 6)

    def test_resample_monthly_fewer_rows(self):
        df = _daily_df(60)
        mo = self.mgr._resample_to_period(df, "monthly")
        self.assertLess(len(mo), len(df))
        self.assertGreater(len(mo), 0)

    def test_resample_handles_missing_columns(self):
        df = _daily_df(20).drop(columns=["amount"])
        wk = self.mgr._resample_to_period(df, "weekly")
        self.assertLess(len(wk), len(df))


class TestStockServicePeriod(unittest.TestCase):
    def test_get_history_weekly(self):
        svc = StockService()
        real = DataFetcherManager()
        with mock.patch("data_provider.base.DataFetcherManager") as Mgr:
            inst = Mgr.return_value
            inst.get_daily_data.side_effect = (
                lambda *a, **k: (
                    real._resample_to_period(_daily_df(30), k.get("period", "daily")),
                    "akshare",
                )
            )
            inst.get_stock_name.return_value = "测试股"
            res = svc.get_history_data("600519", period="weekly", days=6)
        self.assertEqual(res["period"], "weekly")
        self.assertGreater(len(res["data"]), 0)
        self.assertLessEqual(len(res["data"]), 6)
        self.assertEqual(res["stock_name"], "测试股")


if __name__ == "__main__":
    unittest.main()
