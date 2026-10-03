"""Cash-flow forecasting.

What was wrong with the original implementation:

* It fabricated its own history. Thirty days of
  ``22000 + 4000*sin(2*pi*i/7) + np.random.normal(0, 1500)`` were generated, and
  real transaction amounts were merely *added on top* of that synthetic baseline.
  The model was therefore fitted mostly to noise the code had just invented.
* Because fresh ``np.random.normal`` noise was drawn on every call, two identical
  requests returned different forecasts. Nothing was reproducible.
* Amounts were summed without FX conversion, so INR and USD were added together.
* ``inflow = predicted * 1.25`` and ``outflow = predicted * 0.25`` were constants
  with no relationship to the data.
* The project brief specifies Prophet; the implementation used a single-feature
  ``Ridge`` regression on a day index, which cannot represent seasonality.
* Every ``GET /forecast`` deleted the whole table and retrained, making a read
  request destructive and non-idempotent.

This version aggregates real, FX-normalised daily cash movements, fits Prophet
when there is enough history (falling back to Ridge and then to a seasonal naive
estimator), is deterministic, derives inflow/outflow from observed bank
credit/debit behaviour, records which model produced each point, and caches
results so a read does not retrain.
"""

from __future__ import annotations

import logging
import os
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ForecastModel, TransactionType
from app.models.bank_transaction import BankTransaction
from app.models.reconciliation import CashFlowForecast
from app.models.transaction import Transaction
from app.services.fx_service import FXConverter, get_converter

logger = logging.getLogger(__name__)

MODEL_VERSION = "2.0.0"


@dataclass
class DailyPoint:
    """One day of observed cash movement, in the base currency."""

    day: date
    inflow: float = 0.0
    outflow: float = 0.0

    @property
    def net(self) -> float:
        return self.inflow - self.outflow


@dataclass
class ForecastOutput:
    """A fitted forecast plus the provenance needed to defend it."""

    points: List[Dict[str, float]] = field(default_factory=list)
    model_name: str = ForecastModel.SEASONAL_NAIVE.value
    history_days: int = 0
    observed_days: int = 0
    is_synthetic: bool = False
    currency: str = "USD"
    notes: List[str] = field(default_factory=list)


@contextmanager
def _quiet_stan():
    """Silence cmdstanpy/Prophet's very chatty fitting output."""
    loggers = ["cmdstanpy", "prophet", "prophet.models", "matplotlib"]
    previous = {}
    for name in loggers:
        target = logging.getLogger(name)
        previous[name] = target.level
        target.setLevel(logging.CRITICAL)
        target.propagate = False
    os.environ.setdefault("CMDSTANPY_LOG_LEVEL", "CRITICAL")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            yield
        finally:
            for name, level in previous.items():
                logging.getLogger(name).setLevel(level)


# ---------------------------------------------------------------------------
# History construction (real data only)
# ---------------------------------------------------------------------------


def build_daily_history(
    db: Session,
    converter: Optional[FXConverter] = None,
    lookback_days: int = 180,
) -> Tuple[List[DailyPoint], List[str]]:
    """Aggregate observed daily cash movement from the ledgers.

    Bank statement lines are the preferred source because they are actual
    settled cash with an explicit credit/debit direction. When bank coverage is
    thin, Mojaloop transfers are used as the inflow proxy. Everything is summed
    on ``amount_base`` so currencies are comparable.

    Returns the series and any notes about how it was derived.
    """
    converter = converter or get_converter(db)
    notes: List[str] = []
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    buckets: Dict[date, DailyPoint] = {}

    # --- bank settlement lines, grouped in SQL rather than in Python ---
    bank_rows = (
        db.query(
            func.date(BankTransaction.value_date).label("day"),
            BankTransaction.transaction_type,
            func.sum(
                func.coalesce(BankTransaction.amount_base, BankTransaction.amount)
            ).label("total"),
        )
        .filter(BankTransaction.value_date >= cutoff)
        .group_by(func.date(BankTransaction.value_date), BankTransaction.transaction_type)
        .all()
    )

    for day_value, txn_type, total in bank_rows:
        day = _coerce_date(day_value)
        if day is None:
            continue
        point = buckets.setdefault(day, DailyPoint(day=day))
        amount = float(total or 0.0)
        if str(txn_type or "").strip().upper() == TransactionType.DEBIT.value:
            point.outflow += amount
        else:
            point.inflow += amount

    if bank_rows:
        notes.append(
            f"Inflow/outflow derived from {len(bank_rows)} bank statement day-groups "
            f"using CREDIT/DEBIT direction."
        )

    # --- fall back to transfers when bank coverage is thin ---
    if len(buckets) < settings.forecast.min_history_days:
        tx_rows = (
            db.query(
                func.date(Transaction.created_at).label("day"),
                func.sum(
                    func.coalesce(Transaction.amount_base, Transaction.amount)
                ).label("total"),
            )
            .filter(Transaction.created_at >= cutoff)
            .group_by(func.date(Transaction.created_at))
            .all()
        )
        added = 0
        for day_value, total in tx_rows:
            day = _coerce_date(day_value)
            if day is None or day in buckets:
                continue
            buckets[day] = DailyPoint(day=day, inflow=float(total or 0.0))
            added += 1
        if added:
            notes.append(
                f"Bank coverage was below the {settings.forecast.min_history_days}-day minimum; "
                f"added {added} day(s) from Mojaloop transfer volume as an inflow proxy."
            )

    if not buckets:
        return [], notes

    # --- fill gaps so the series is continuous ---
    ordered_days = sorted(buckets)
    first, last = ordered_days[0], ordered_days[-1]
    series: List[DailyPoint] = []
    cursor = first
    filled = 0
    while cursor <= last:
        if cursor in buckets:
            series.append(buckets[cursor])
        else:
            series.append(DailyPoint(day=cursor))
            filled += 1
        cursor += timedelta(days=1)

    if filled:
        notes.append(f"Filled {filled} day(s) with zero movement to keep the series continuous.")

    notes.append(f"Observed window: {first.isoformat()} to {last.isoformat()}.")
    return series, notes


def _coerce_date(value) -> Optional[date]:
    """SQLite's ``date()`` returns a string; PostgreSQL returns a date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def _prophet_available() -> bool:
    try:
        import prophet  # noqa: F401

        return True
    except Exception:
        return False


def _fit_prophet(
    series: Sequence[DailyPoint], horizon: int
) -> Optional[List[Tuple[date, float, float, float]]]:
    """Fit Prophet and return (day, yhat, lower, upper) for the horizon."""
    try:
        import pandas as pd
        from prophet import Prophet
    except Exception:  # pragma: no cover
        return None

    frame = pd.DataFrame(
        {
            "ds": [pd.Timestamp(point.day) for point in series],
            "y": [point.net for point in series],
        }
    )

    try:
        with _quiet_stan():
            np.random.seed(settings.forecast.random_seed)
            model = Prophet(
                interval_width=0.95,
                weekly_seasonality=True,
                yearly_seasonality=False,
                daily_seasonality=False,
                seasonality_mode="additive",
                changepoint_prior_scale=0.05,
            )
            model.fit(frame)
            future = model.make_future_dataframe(periods=horizon, freq="D")
            prediction = model.predict(future)
    except Exception as exc:
        logger.warning("Prophet fit failed (%s); falling back", exc)
        return None

    tail = prediction.tail(horizon)
    results: List[Tuple[date, float, float, float]] = []
    for _, row in tail.iterrows():
        results.append(
            (
                row["ds"].date(),
                float(row["yhat"]),
                float(row["yhat_lower"]),
                float(row["yhat_upper"]),
            )
        )
    return results


def _fit_ridge(
    series: Sequence[DailyPoint], horizon: int
) -> Optional[List[Tuple[date, float, float, float]]]:
    """Linear trend plus day-of-week dummies, with residual-based bounds.

    Retained as the fallback when Prophet is unavailable. Unlike the original
    single-feature version, this at least encodes weekly seasonality.
    """
    if len(series) < 4:
        return None

    try:
        from sklearn.linear_model import Ridge
    except Exception:  # pragma: no cover
        return None

    values = np.array([point.net for point in series], dtype=float)
    indices = np.arange(len(series), dtype=float)

    def design(idx: np.ndarray, weekdays: np.ndarray) -> np.ndarray:
        dummies = np.zeros((len(idx), 7))
        dummies[np.arange(len(idx)), weekdays] = 1.0
        return np.column_stack([idx, dummies])

    history_weekdays = np.array([point.day.weekday() for point in series])
    features = design(indices, history_weekdays)

    model = Ridge(alpha=1.0)
    model.fit(features, values)

    residuals = values - model.predict(features)
    spread = float(np.std(residuals)) if len(residuals) > 1 else abs(float(values.mean())) * 0.2
    margin = 1.96 * spread

    last_day = series[-1].day
    future_days = [last_day + timedelta(days=offset + 1) for offset in range(horizon)]
    future_indices = np.arange(len(series), len(series) + horizon, dtype=float)
    future_weekdays = np.array([day.weekday() for day in future_days])
    predictions = model.predict(design(future_indices, future_weekdays))

    return [
        (day, float(value), float(value - margin), float(value + margin))
        for day, value in zip(future_days, predictions)
    ]


def _fit_seasonal_naive(
    series: Sequence[DailyPoint], horizon: int
) -> List[Tuple[date, float, float, float]]:
    """Last-resort estimator: per-weekday mean with an empirical spread.

    Used when there is too little history to fit anything. Reporting this
    honestly is better than fitting a trend to three data points.
    """
    by_weekday: Dict[int, List[float]] = {}
    for point in series:
        by_weekday.setdefault(point.day.weekday(), []).append(point.net)

    overall = [point.net for point in series] or [0.0]
    overall_mean = float(np.mean(overall))
    spread = float(np.std(overall)) if len(overall) > 1 else abs(overall_mean) * 0.25
    margin = 1.96 * spread

    last_day = series[-1].day if series else datetime.utcnow().date()
    results: List[Tuple[date, float, float, float]] = []
    for offset in range(horizon):
        day = last_day + timedelta(days=offset + 1)
        samples = by_weekday.get(day.weekday())
        value = float(np.mean(samples)) if samples else overall_mean
        results.append((day, value, value - margin, value + margin))
    return results


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _observed_averages(series: Sequence[DailyPoint]) -> Tuple[float, float, float]:
    """Mean daily inflow, outflow and net actually observed.

    Replaces the hardcoded ``predicted * 1.25`` / ``predicted * 0.25``
    multipliers. Projected gross figures are scaled from these observed means in
    proportion to the predicted net, which keeps them tied to how money has
    really moved and stays numerically stable when inflow and outflow are close
    (where a share-based derivation would blow up as net approaches zero).
    """
    if not series:
        return 0.0, 0.0, 0.0
    count = float(len(series))
    mean_in = sum(point.inflow for point in series) / count
    mean_out = sum(point.outflow for point in series) / count
    mean_net = sum(point.net for point in series) / count
    return mean_in, mean_out, mean_net


def _scale_factor(predicted_net: float, mean_net: float) -> float:
    """How far the prediction departs from the historical daily average.

    Clamped so one unusual prediction cannot produce an absurd gross figure.
    """
    if abs(mean_net) < 1e-9:
        return 1.0
    return float(min(4.0, max(0.25, abs(predicted_net) / abs(mean_net))))


def _split_gross(predicted_net: float, mean_in: float, mean_out: float, mean_net: float) -> Tuple[float, float]:
    """Split a predicted net figure into gross inflow and outflow.

    Solved from two equations so the reported numbers are internally consistent:

        inflow - outflow = predicted_net      (the forecast must reconcile)
        inflow + outflow = gross              (throughput scaled from history)

    giving ``inflow = (gross + net) / 2`` and ``outflow = (gross - net) / 2``.
    ``gross`` is floored at ``abs(net)`` so neither leg can come out negative.

    Deriving each leg independently (scaling ``mean_in`` and ``mean_out`` by the
    same factor) does *not* preserve the first equation once the predicted net
    changes sign, which is what the test suite caught.
    """
    historical_gross = max(mean_in + mean_out, 0.0)
    factor = _scale_factor(predicted_net, mean_net)
    gross = max(historical_gross * factor, abs(predicted_net))

    inflow = (gross + predicted_net) / 2.0
    outflow = (gross - predicted_net) / 2.0
    return max(inflow, 0.0), max(outflow, 0.0)


def compute_forecast(
    db: Session, days: int, converter: Optional[FXConverter] = None
) -> ForecastOutput:
    """Build the history, fit the best available model, and shape the output."""
    config = settings.forecast
    horizon = max(1, min(days, config.max_horizon_days))
    converter = converter or get_converter(db)

    series, notes = build_daily_history(db, converter)
    output = ForecastOutput(currency=converter.base_currency, notes=notes)
    output.observed_days = len(series)

    if not series:
        if not config.allow_synthetic_padding:
            output.notes.append(
                "No observed cash movement and synthetic padding is disabled; "
                "no forecast produced."
            )
            return output
        # With no data at all, state plainly that the output is a placeholder
        # rather than quietly inventing a trend as the old code did.
        output.is_synthetic = True
        output.notes.append(
            "No transaction history available. Returning a flat zero baseline "
            "explicitly marked synthetic."
        )
        today = datetime.utcnow().date()
        series = [DailyPoint(day=today - timedelta(days=offset)) for offset in range(14, 0, -1)]

    output.history_days = len(series)

    predictions: Optional[List[Tuple[date, float, float, float]]] = None

    if (
        config.prefer_prophet
        and len(series) >= config.min_history_days
        and _prophet_available()
    ):
        predictions = _fit_prophet(series, horizon)
        if predictions is not None:
            output.model_name = ForecastModel.PROPHET.value

    if predictions is None:
        predictions = _fit_ridge(series, horizon)
        if predictions is not None:
            output.model_name = ForecastModel.RIDGE.value
            if config.prefer_prophet and len(series) < config.min_history_days:
                output.notes.append(
                    f"Only {len(series)} day(s) of history; Prophet needs at least "
                    f"{config.min_history_days}. Used ridge regression with weekday "
                    f"seasonality instead."
                )

    if predictions is None:
        predictions = _fit_seasonal_naive(series, horizon)
        output.model_name = ForecastModel.SEASONAL_NAIVE.value
        output.notes.append(
            "Insufficient history to fit a regression; used a per-weekday mean."
        )

    mean_in, mean_out, mean_net = _observed_averages(series)

    # Domain constraint: if the observed series never went negative (a
    # credit-only settlement ledger), a negative net prediction is an
    # extrapolation artefact rather than a real signal. Floor it at zero instead
    # of reporting a negative cash position that cannot occur.
    never_negative = all(point.net >= 0 for point in series)
    if never_negative:
        clamped = [
            (day, max(0.0, value), max(0.0, lower), max(0.0, upper))
            for day, value, lower, upper in predictions
        ]
        if any(
            original[1] < 0 for original in predictions
        ):  # only note it when it actually bit
            output.notes.append(
                "Observed history contains no net outflow day, so negative predictions "
                "were floored at zero."
            )
        predictions = clamped

    for day, value, lower, upper in predictions:
        projected_in, projected_out = _split_gross(value, mean_in, mean_out, mean_net)

        output.points.append(
            {
                "date": day.isoformat(),
                "predicted_amount": round(value, 2),
                "lower_bound": round(lower, 2),
                "upper_bound": round(upper, 2),
                "inflow": round(projected_in, 2),
                "outflow": round(projected_out, 2),
            }
        )

    output.notes.append(
        f"Observed daily averages used for the gross split: inflow {mean_in:,.2f}, "
        f"outflow {mean_out:,.2f}, net {mean_net:,.2f} {output.currency}."
    )
    return output


def generate_forecast(
    db: Session,
    days: int = None,
    force: bool = True,
    converter: Optional[FXConverter] = None,
) -> List[CashFlowForecast]:
    """Fit and persist a forecast. Returns the stored rows.

    ``force=False`` reuses a cached forecast that is still inside the TTL and
    already covers the requested horizon, which is what makes ``GET /forecast``
    non-destructive.
    """
    config = settings.forecast
    horizon = max(1, min(days or config.default_horizon_days, config.max_horizon_days))

    if not force:
        cached = _cached_forecast(db, horizon)
        if cached is not None:
            return cached

    output = compute_forecast(db, horizon, converter)
    now = datetime.utcnow()

    # Replace the previous forecast set in the same transaction as the insert, so
    # a failure cannot leave the table empty.
    db.query(CashFlowForecast).delete(synchronize_session=False)

    rows: List[CashFlowForecast] = []
    for point in output.points:
        row = CashFlowForecast(
            forecast_date=datetime.fromisoformat(point["date"]),
            predicted_amount=point["predicted_amount"],
            lower_bound=point["lower_bound"],
            upper_bound=point["upper_bound"],
            inflow=point["inflow"],
            outflow=point["outflow"],
            created_at=now,
            generated_at=now,
            model_name=output.model_name,
            model_version=MODEL_VERSION,
            currency=output.currency,
            horizon_days=horizon,
            history_days=output.history_days,
            is_synthetic=1 if output.is_synthetic else 0,
        )
        db.add(row)
        rows.append(row)

    db.commit()
    logger.info(
        "Forecast generated: model=%s horizon=%dd history=%dd currency=%s synthetic=%s",
        output.model_name,
        horizon,
        output.history_days,
        output.currency,
        output.is_synthetic,
    )
    return rows


def _cached_forecast(db: Session, horizon: int) -> Optional[List[CashFlowForecast]]:
    """Return a still-valid stored forecast covering ``horizon`` days."""
    newest = (
        db.query(func.max(CashFlowForecast.generated_at)).scalar()
        or db.query(func.max(CashFlowForecast.created_at)).scalar()
    )
    if newest is None:
        return None

    age = (datetime.utcnow() - newest).total_seconds()
    if age > settings.forecast.cache_ttl_seconds:
        return None

    rows = (
        db.query(CashFlowForecast)
        .order_by(CashFlowForecast.forecast_date.asc())
        .all()
    )
    if len(rows) < horizon:
        return None

    logger.debug("Serving cached forecast (%.0fs old)", age)
    return rows[:horizon]


def get_forecast_points(
    db: Session, days: int = None, force: bool = False
) -> List[Dict[str, object]]:
    """Read-oriented accessor used by the API.

    Regenerates only when the cache is stale or does not cover the horizon.
    """
    rows = generate_forecast(db, days=days, force=force)
    return [
        {
            "date": row.forecast_date.strftime("%Y-%m-%d"),
            "predicted_amount": row.predicted_amount,
            "lower_bound": row.lower_bound,
            "upper_bound": row.upper_bound,
            "inflow": row.inflow,
            "outflow": row.outflow,
            "model": row.model_name,
            "currency": row.currency,
            "is_synthetic": bool(row.is_synthetic),
        }
        for row in rows
    ]


def forecast_metadata(db: Session) -> Dict[str, object]:
    """Describe the stored forecast: model, coverage, freshness."""
    row = (
        db.query(CashFlowForecast)
        .order_by(CashFlowForecast.generated_at.desc(), CashFlowForecast.id.desc())
        .first()
    )
    if row is None:
        return {
            "available": False,
            "prophet_installed": _prophet_available(),
            "message": "No forecast has been generated yet.",
        }

    generated = row.generated_at or row.created_at
    return {
        "available": True,
        "model": row.model_name,
        "model_version": row.model_version,
        "currency": row.currency,
        "horizon_days": row.horizon_days,
        "history_days": row.history_days,
        "is_synthetic": bool(row.is_synthetic),
        "generated_at": generated.isoformat() if generated else None,
        "age_seconds": (
            round((datetime.utcnow() - generated).total_seconds(), 1) if generated else None
        ),
        "cache_ttl_seconds": settings.forecast.cache_ttl_seconds,
        "prophet_installed": _prophet_available(),
        "points": db.query(func.count(CashFlowForecast.id)).scalar(),
    }


__all__ = [
    "DailyPoint",
    "ForecastOutput",
    "MODEL_VERSION",
    "build_daily_history",
    "compute_forecast",
    "forecast_metadata",
    "generate_forecast",
    "get_forecast_points",
]
