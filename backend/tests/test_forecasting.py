import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import sys

from app.services.forecasting import (
    _baseline_forecast,
    forecast_cash_flows,
    forecast_cash_positions,
    weekly_cash_positions,
)


class ForecastingTests(unittest.TestCase):
    def test_baseline_forecast_uses_weekday_history(self):
        dates = pd.date_range("2026-10-01", periods=28)
        history = pd.DataFrame({
            "ds": dates,
            "y": [100.0 + day.weekday() * 10 for day in dates],
        })

        forecast = _baseline_forecast(history, 7)

        self.assertEqual(len(forecast["yhat"].unique()), 7)

    def test_prophet_is_used_with_at_least_fourteen_daily_points(self):
        class FakeProphet:
            def __init__(self, **kwargs):
                pass

            def fit(self, history):
                return self

            def make_future_dataframe(self, periods):
                return pd.DataFrame({
                    "ds": pd.date_range("2026-11-15", periods=periods),
                })

            def predict(self, future):
                result = future.copy()
                result["yhat"] = 100.0
                result["yhat_lower"] = 90.0
                result["yhat_upper"] = 110.0
                return result

        history = pd.DataFrame({
            "ds": pd.date_range("2026-10-01", periods=14),
            "y": np.arange(14, dtype=float) + 100,
        })

        with patch.dict(sys.modules, {"prophet": SimpleNamespace(Prophet=FakeProphet)}):
            forecast = forecast_cash_positions(history, periods=3)

        self.assertEqual(forecast.attrs["method"], "prophet")
        self.assertFalse(forecast.attrs["used_fallback"])
        self.assertEqual(len(forecast), 3)

    def test_sparse_history_uses_robust_baseline_instead_of_prophet(self):
        history = pd.DataFrame({
            "ds": pd.date_range("2026-11-01", periods=4),
            "y": [72_000_000.0, 6_000_000.0, 3_000_000.0, 1_800_000.0],
        })

        with patch("app.services.forecasting._baseline_forecast", wraps=_baseline_forecast) as baseline:
            forecast = forecast_cash_positions(history, periods=7)

        baseline.assert_called_once()
        self.assertTrue(forecast.attrs["used_fallback"])
        self.assertEqual(forecast.attrs["method"], "baseline")
        self.assertTrue(np.all(forecast["yhat"] > 0))
        self.assertGreater(forecast["yhat"].nunique(), 1)
        self.assertGreater(forecast["yhat_lower"].nunique(), 1)
        self.assertGreater(forecast["yhat_upper"].nunique(), 1)

    def test_cash_flow_forecast_keeps_gross_flows_separate_from_net(self):
        def fake_forecast(history, periods):
            column = history["y"].iloc[0]
            value = 120.0 if column == 150.0 else 40.0
            result = pd.DataFrame({
                "ds": pd.date_range("2026-11-01", periods=periods),
                "yhat": [value] * periods,
                "yhat_lower": [value - 10] * periods,
                "yhat_upper": [value + 10] * periods,
            })
            result.attrs["method"] = "baseline"
            result.attrs["used_fallback"] = True
            return result

        daily_flows = pd.DataFrame({
            "ds": [pd.Timestamp("2026-10-31")],
            "inflow": [150.0],
            "outflow": [40.0],
        })
        with patch(
            "app.services.forecasting.forecast_cash_positions",
            side_effect=fake_forecast,
        ):
            forecast = forecast_cash_flows(daily_flows, periods=2)

        self.assertEqual(forecast.iloc[0]["inflow"], 120.0)
        self.assertEqual(forecast.iloc[0]["outflow"], 40.0)
        self.assertEqual(forecast.iloc[0]["yhat"], 80.0)

    def test_weekly_forecast_sums_gross_flow_columns(self):
        daily = pd.DataFrame({
            "ds": pd.date_range("2026-11-02", periods=3),
            "yhat": [10.0, 20.0, 30.0],
            "yhat_lower": [8.0, 18.0, 28.0],
            "yhat_upper": [12.0, 22.0, 32.0],
            "inflow": [100.0, 200.0, 300.0],
            "outflow": [50.0, 60.0, 70.0],
        })

        weekly = weekly_cash_positions(daily)

        self.assertEqual(len(weekly), 1)
        self.assertEqual(weekly.iloc[0]["inflow"], 600.0)
        self.assertEqual(weekly.iloc[0]["outflow"], 180.0)
        self.assertEqual(weekly.iloc[0]["yhat"], 60.0)


if __name__ == "__main__":
    unittest.main()
