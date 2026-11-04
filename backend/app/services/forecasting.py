"""Prophet-based daily and weekly cash-position forecasts."""

import logging
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
FORECAST_COLUMNS = ["ds", "yhat", "yhat_lower", "yhat_upper"]


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


def _dummy_history(history: pd.DataFrame) -> pd.DataFrame:
    if not history.empty:
        end_date = pd.Timestamp(history["ds"].max()).normalize()
        baseline = float(history["y"].median())
    else:
        end_date = pd.Timestamp(datetime.utcnow().date() - timedelta(days=1))
        baseline = 0.0

    if not pd.notna(baseline):
        baseline = 0.0
    dates = pd.date_range(end=end_date, periods=30, freq="D")
    # Small deterministic weekly shape keeps the fallback stable across requests.
    values = [baseline * (1.0 + 0.05 * ((day.weekday() - 3) / 3)) for day in dates]
    return pd.DataFrame({"ds": dates, "y": values})


def _baseline_forecast(history: pd.DataFrame, periods: int) -> pd.DataFrame:
    if history.empty:
        level = 0.0
        spread = 1.0
        start_date = pd.Timestamp(datetime.utcnow().date())
    else:
        values = history["y"].tail(30).astype(float)
        level = float(values.mean())
        spread = max(float(values.std(ddof=0)) if len(values) > 1 else 0.0, abs(level) * 0.1, 1.0)
        start_date = pd.Timestamp(history["ds"].max()).normalize() + pd.Timedelta(days=1)

    future_dates = pd.date_range(start=start_date, periods=periods, freq="D")
    forecast = pd.DataFrame({"ds": future_dates, "yhat": level})
    forecast["yhat_lower"] = level - 1.96 * spread
    forecast["yhat_upper"] = level + 1.96 * spread
    return forecast[FORECAST_COLUMNS]


def forecast_cash_positions(historical_data: Any, periods: int = 30) -> pd.DataFrame:
    """Return daily Prophet predictions, falling back to a stable baseline on errors."""
    periods = max(1, min(int(periods), 365))
    try:
        history = _daily_history(historical_data)
    except Exception:
        logger.exception("Could not normalize cash-position history; using fallback data")
        history = pd.DataFrame(columns=["ds", "y"])

    used_fallback = len(history) < 3
    training_data = _dummy_history(history) if used_fallback else history

    try:
        from prophet import Prophet

        model = Prophet(
            yearly_seasonality=False,
            weekly_seasonality=len(training_data) >= 14,
            daily_seasonality=False,
            uncertainty_samples=100,
        )
        model.fit(training_data[["ds", "y"]])
        future = model.make_future_dataframe(periods=periods)
        forecast = model.predict(future).tail(periods)[FORECAST_COLUMNS].reset_index(drop=True)
        forecast.attrs["used_fallback"] = used_fallback
        forecast.attrs["method"] = "prophet"
        return forecast
    except Exception:
        logger.exception("Prophet forecast failed; returning a baseline forecast")
        forecast = _baseline_forecast(training_data if used_fallback else history, periods)
        forecast.attrs["used_fallback"] = True
        forecast.attrs["method"] = "baseline"
        return forecast


def weekly_cash_positions(daily_forecast: pd.DataFrame) -> pd.DataFrame:
    """Roll daily forecast values into weekly cash-position estimates."""
    if daily_forecast.empty:
        return pd.DataFrame(columns=FORECAST_COLUMNS)
    frame = daily_forecast.copy()
    frame["ds"] = pd.to_datetime(frame["ds"], errors="coerce")
    frame = frame.dropna(subset=["ds"])
    frame["week_start"] = frame["ds"].dt.to_period("W-SUN").dt.start_time
    weekly = frame.groupby("week_start", as_index=False)[
        ["yhat", "yhat_lower", "yhat_upper"]
    ].sum().rename(columns={"week_start": "ds"})
    weekly.attrs.update(daily_forecast.attrs)
    return weekly[FORECAST_COLUMNS]
