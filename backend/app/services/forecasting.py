"""Prophet-based daily and weekly cash-position forecasts."""

import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
FORECAST_COLUMNS = ["ds", "yhat", "yhat_lower", "yhat_upper"]
MIN_PROPHET_HISTORY_DAYS = 14
BASELINE_WEEKDAY_FACTORS = (0.95, 0.975, 1.0, 1.025, 1.05, 1.025, 0.97)


def _records_to_frame(data: Any) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data.copy()

    if isinstance(data, Session):
        from app.models.transaction import Transaction

        rows = data.query(
            Transaction.created_at.label("timestamp"),
            Transaction.amount,
        ).filter(Transaction.created_at.isnot(None)).all()
        return pd.DataFrame(
            [{"timestamp": row[0], "amount": row[1]} for row in rows]
        )

    records = []
    for row in data or []:
        if hasattr(row, "_mapping"):
            records.append(dict(row._mapping))
        elif isinstance(row, dict):
            records.append(row)
        elif hasattr(row, "__dict__"):
            records.append(vars(row))
        else:
            records.append(row)
    return pd.DataFrame(records)


def _daily_history(data: Any) -> pd.DataFrame:
    frame = _records_to_frame(data)
    if frame.empty:
        return pd.DataFrame(columns=["ds", "y"])

    normalized_columns = {str(column).lower(): column for column in frame.columns}
    date_column = next(
        (normalized_columns[name] for name in ("timestamp", "settlement_date", "created_at", "date", "ds") if name in normalized_columns),
        None,
    )
    amount_column = normalized_columns.get("amount", normalized_columns.get("y"))
    if date_column is None or amount_column is None:
        return pd.DataFrame(columns=["ds", "y"])

    timestamps = pd.to_datetime(frame[date_column], errors="coerce", utc=True)
    amounts = pd.to_numeric(frame[amount_column], errors="coerce")
    clean = pd.DataFrame({"ds": timestamps.dt.tz_convert(None).dt.normalize(), "y": amounts})
    clean = clean.dropna(subset=["ds", "y"])
    if clean.empty:
        return pd.DataFrame(columns=["ds", "y"])
    return clean.groupby("ds", as_index=False, sort=True)["y"].sum()


def _baseline_forecast(history: pd.DataFrame, periods: int) -> pd.DataFrame:
    if history.empty:
        level = 0.0
        spread = 1.0
        start_date = pd.Timestamp(datetime.utcnow().date())
        weekday_levels = {}
    else:
        recent_history = history.tail(30).copy()
        values = recent_history["y"].astype(float)
        level = float(values.median())
        median_absolute_deviation = float(np.median(np.abs(values - level)))
        spread = max(1.4826 * median_absolute_deviation, abs(level) * 0.1, 1.0)
        start_date = pd.Timestamp(history["ds"].max()).normalize() + pd.Timedelta(days=1)
        weekday_history = recent_history.assign(
            weekday=pd.to_datetime(recent_history["ds"]).dt.weekday
        )
        weekday_groups = weekday_history.groupby("weekday")["y"]
        weekday_levels = {
            weekday: float(group.median())
            for weekday, group in weekday_groups
            if len(group) >= 2
        }
        weekday_levels.update({
            weekday: level * factor
            for weekday, factor in enumerate(BASELINE_WEEKDAY_FACTORS)
            if weekday not in weekday_levels
        })

    future_dates = pd.date_range(start=start_date, periods=periods, freq="D")
    forecast_values = [
        float(weekday_levels.get(future_date.weekday(), level))
        for future_date in future_dates
    ]
    forecast = pd.DataFrame({"ds": future_dates, "yhat": forecast_values})
    forecast["yhat_lower"] = forecast["yhat"] - 1.96 * spread
    forecast["yhat_upper"] = forecast["yhat"] + 1.96 * spread
    return forecast[FORECAST_COLUMNS]


def forecast_cash_positions(historical_data: Any, periods: int = 30) -> pd.DataFrame:
    """Return daily Prophet predictions, falling back to a stable baseline on errors."""
    periods = max(1, min(int(periods), 365))
    try:
        history = _daily_history(historical_data)
    except Exception:
        logger.exception("Could not normalize cash-position history; using fallback data")
        history = pd.DataFrame(columns=["ds", "y"])

    if len(history) < MIN_PROPHET_HISTORY_DAYS:
        forecast = _baseline_forecast(history, periods)
        forecast.attrs["used_fallback"] = True
        forecast.attrs["method"] = "baseline"
        return forecast

    try:
        from prophet import Prophet

        model = Prophet(
            yearly_seasonality=False,
            weekly_seasonality=True,
            daily_seasonality=False,
            uncertainty_samples=100,
        )
        model.fit(history[["ds", "y"]])
        future = model.make_future_dataframe(periods=periods)
        forecast = model.predict(future).tail(periods)[FORECAST_COLUMNS].reset_index(drop=True)
        forecast.attrs["used_fallback"] = False
        forecast.attrs["method"] = "prophet"
        return forecast
    except Exception:
        logger.exception("Prophet forecast failed; returning a baseline forecast")
        forecast = _baseline_forecast(history, periods)
        forecast.attrs["used_fallback"] = True
        forecast.attrs["method"] = "baseline"
        return forecast


def forecast_cash_flows(daily_flows: pd.DataFrame, periods: int = 30) -> pd.DataFrame:
    """Forecast gross inflow and outflow separately, then derive net position."""
    if daily_flows.empty:
        daily_flows = pd.DataFrame(columns=["ds", "inflow", "outflow"])

    inflow_history = daily_flows[["ds", "inflow"]].rename(columns={"inflow": "y"})
    outflow_history = daily_flows[["ds", "outflow"]].rename(columns={"outflow": "y"})
    inflow_forecast = forecast_cash_positions(inflow_history, periods)
    outflow_forecast = forecast_cash_positions(outflow_history, periods)

    forecast = pd.DataFrame({
        "ds": inflow_forecast["ds"],
        "yhat": inflow_forecast["yhat"] - outflow_forecast["yhat"],
        "yhat_lower": inflow_forecast["yhat_lower"] - outflow_forecast["yhat_upper"],
        "yhat_upper": inflow_forecast["yhat_upper"] - outflow_forecast["yhat_lower"],
        "inflow": inflow_forecast["yhat"].clip(lower=0),
        "outflow": outflow_forecast["yhat"].clip(lower=0),
    })
    forecast.attrs["used_fallback"] = (
        inflow_forecast.attrs.get("used_fallback", False)
        or outflow_forecast.attrs.get("used_fallback", False)
    )
    forecast.attrs["method"] = (
        "prophet"
        if inflow_forecast.attrs.get("method") == "prophet"
        and outflow_forecast.attrs.get("method") == "prophet"
        else "baseline"
    )
    return forecast


def weekly_cash_positions(daily_forecast: pd.DataFrame) -> pd.DataFrame:
    """Roll daily forecast values into weekly cash-position estimates."""
    if daily_forecast.empty:
        return pd.DataFrame(columns=FORECAST_COLUMNS)
    frame = daily_forecast.copy()
    frame["ds"] = pd.to_datetime(frame["ds"], errors="coerce")
    frame = frame.dropna(subset=["ds"])
    frame["week_start"] = frame["ds"].dt.to_period("W-SUN").dt.start_time
    sum_columns = [
        column
        for column in ("yhat", "yhat_lower", "yhat_upper", "inflow", "outflow")
        if column in frame.columns
    ]
    weekly = frame.groupby("week_start", as_index=False)[sum_columns].sum().rename(
        columns={"week_start": "ds"}
    )
    weekly.attrs.update(daily_forecast.attrs)
    return weekly
