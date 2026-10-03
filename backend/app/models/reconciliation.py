from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text

from app.core.constants import (
    AnomalyStatus,
    ConfidenceTier,
    LedgerType,
    MatchStatus,
    Severity,
)
from app.database.db import Base


class Reconciliation(Base):
    """One row per transaction per reconciliation run: the match outcome.

    Legal values for the string columns are defined in ``app.core.constants``
    rather than in comments.
    """

    __tablename__ = "reconciliations"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(String, index=True, nullable=False)
    ledger_id = Column(String, index=True, nullable=True)  # Bank/ERP ID matched against
    ledger_type = Column(String, default=LedgerType.BANK.value)  # see LedgerType
    status = Column(String, default=MatchStatus.UNMATCHED.value, index=True)  # see MatchStatus
    amount_difference = Column(Float, default=0.0)
    match_confidence = Column(Float, default=0.0)  # 0 to 100 percentage
    confidence_tier = Column(String, default=ConfidenceTier.LOW.value, index=True)
    remarks = Column(Text, nullable=True)
    reconciled_at = Column(DateTime, default=datetime.utcnow)

    # Score breakdown, so a confidence number can be explained rather than
    # just asserted. Populated by the matching engine.
    score_amount = Column(Float, default=0.0)
    score_name = Column(Float, default=0.0)
    score_reference = Column(Float, default=0.0)
    score_date = Column(Float, default=0.0)

    # Normalised comparison values actually used for the decision.
    base_currency = Column(String, nullable=True)
    transaction_amount_base = Column(Float, nullable=True)
    ledger_amount_base = Column(Float, nullable=True)
    date_difference_days = Column(Float, nullable=True)

    # True when a human confirmed this match rather than the engine.
    is_manual = Column(Integer, default=0)

    __table_args__ = (
        Index("ix_reconciliations_status_tier", "status", "confidence_tier"),
    )


class Anomaly(Base):
    """A flagged exception with a human-readable explanation.

    See ``app.core.constants.AnomalyType`` for the full set of types the engine
    emits, and ``AnomalyStatus`` for the lifecycle.
    """

    __tablename__ = "anomalies"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(String, index=True, nullable=False)
    anomaly_type = Column(String, nullable=False, index=True)  # see AnomalyType
    severity = Column(String, default=Severity.MEDIUM.value, index=True)
    risk_score = Column(Float, default=0.5)  # 0.0 to 1.0
    explanation = Column(Text, nullable=False)
    detected_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default=AnomalyStatus.OPEN.value, index=True)

    # Which run raised it, and what the detector was. Lets resolved exceptions
    # survive a re-run instead of being deleted and silently re-created.
    run_id = Column(Integer, index=True, nullable=True)
    detector = Column(String, nullable=True)  # rule / isolation_forest / zscore
    resolved_at = Column(DateTime, nullable=True)
    resolution_note = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_anomalies_status_severity", "status", "severity"),
        Index("ix_anomalies_txn_type", "transaction_id", "anomaly_type"),
    )


class ReconciliationRun(Base):
    """One row per reconciliation execution.

    Replaces the previous ``DELETE FROM reconciliations`` pattern as the unit of
    auditability: every run records its parameters, counts and outcome, so two
    runs can be compared and a scheduled run leaves a trace.
    """

    __tablename__ = "reconciliation_runs"

    id = Column(Integer, primary_key=True, index=True)
    started_at = Column(DateTime, default=datetime.utcnow, index=True)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, default="running", index=True)  # running / success / failed
    trigger = Column(String, default="manual")  # manual / scheduled / webhook / seed

    transactions_examined = Column(Integer, default=0)
    candidates_evaluated = Column(Integer, default=0)
    matched_count = Column(Integer, default=0)
    partial_count = Column(Integer, default=0)
    unmatched_count = Column(Integer, default=0)
    flagged_count = Column(Integer, default=0)
    anomaly_count = Column(Integer, default=0)
    manual_overrides_applied = Column(Integer, default=0)

    duration_seconds = Column(Float, default=0.0)
    base_currency = Column(String, nullable=True)
    parameters_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    # What this run covered. A run scoped to a settlement window only replaces
    # results for transactions inside that window, so two windows can be
    # reconciled independently without one wiping the other's output.
    scope = Column(String, default="full", index=True)  # see RunScope
    settlement_window_id = Column(String, nullable=True, index=True)
    scope_from = Column(DateTime, nullable=True)
    scope_to = Column(DateTime, nullable=True)


class ManualMatch(Base):
    """A treasury user's authoritative override of the engine's decision.

    Stored separately from ``reconciliations`` so that re-running reconciliation
    cannot discard it. The engine re-applies every override at the end of each
    run. Previously a manual match was written straight into ``reconciliations``
    and was destroyed by the next run's ``DELETE``.
    """

    __tablename__ = "manual_matches"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(String, index=True, nullable=False, unique=True)
    ledger_id = Column(String, nullable=False)
    ledger_type = Column(String, default=LedgerType.BANK.value)
    remarks = Column(Text, nullable=True)
    created_by = Column(String, default="treasury-user")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Integer, default=1)


class CashFlowForecast(Base):
    """A single predicted day of net cash flow, in the base currency."""

    __tablename__ = "cash_flow_forecasts"

    id = Column(Integer, primary_key=True, index=True)
    forecast_date = Column(DateTime, nullable=False, index=True)
    predicted_amount = Column(Float, nullable=False)
    lower_bound = Column(Float, nullable=False)
    upper_bound = Column(Float, nullable=False)
    inflow = Column(Float, default=0.0)
    outflow = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Provenance: which model produced this point, over what history, in what
    # currency, and whether any synthetic padding was involved. Without these a
    # forecast number cannot be defended in a review.
    model_name = Column(String, nullable=True)  # see ForecastModel
    model_version = Column(String, nullable=True)
    currency = Column(String, nullable=True)
    horizon_days = Column(Integer, nullable=True)
    history_days = Column(Integer, nullable=True)
    is_synthetic = Column(Integer, default=0)
    generated_at = Column(DateTime, default=datetime.utcnow, index=True)


class FXRate(Base):
    """A currency conversion factor with a validity window.

    ``rate`` is the multiplier that converts one unit of ``from_currency`` into
    ``to_currency``. The validity window is what makes a historical
    reconciliation result reproducible: a run dated last month can resolve the
    rate that was in force then rather than today's rate.

    ``valid_to IS NULL`` marks the currently effective rate for a pair.
    """

    __tablename__ = "fx_rates"

    id = Column(Integer, primary_key=True, index=True)
    from_currency = Column(String, nullable=False, index=True)
    to_currency = Column(String, nullable=False, index=True)
    rate = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow)

    valid_from = Column(DateTime, nullable=True, index=True)
    valid_to = Column(DateTime, nullable=True, index=True)
    source = Column(String, nullable=True)  # static-default / database / provider name

    __table_args__ = (
        Index("ix_fx_rates_pair_valid", "from_currency", "to_currency", "valid_to"),
    )
