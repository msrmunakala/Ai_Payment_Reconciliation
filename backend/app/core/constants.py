"""Canonical status / category values used across models, services and APIs.

These were previously free-text strings whose legal values existed only in
trailing ``#`` comments on the model columns, which allowed typos to silently
produce empty filter results. They are ``str`` enums so they serialise exactly
as before (no database migration required) while being validatable.
"""

from __future__ import annotations

from enum import Enum
from typing import List


class StrEnum(str, Enum):
    """``str`` subclass enum: compares and serialises as its plain value."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)

    @classmethod
    def values(cls) -> List[str]:
        return [member.value for member in cls]

    @classmethod
    def has_value(cls, value: object) -> bool:
        return isinstance(value, str) and value in cls.values()


class MatchStatus(StrEnum):
    """Reconciliation outcome. Lowercase, matching the existing stored data."""

    MATCHED = "matched"
    PARTIAL = "partial"
    UNMATCHED = "unmatched"
    FLAGGED = "flag_for_review"


class ConfidenceTier(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class LedgerType(StrEnum):
    BANK = "BANK"
    ERP = "ERP"
    MOJALOOP = "MOJALOOP"
    NONE = "NONE"


class AnomalyType(StrEnum):
    DUPLICATE_CHARGE = "duplicate_charge"
    AMOUNT_MISMATCH = "amount_mismatch"
    MISSING_SETTLEMENT = "missing_settlement"
    TIMING_ANOMALY = "timing_anomaly"
    UNRECOGNIZED_ACCOUNT = "unrecognized_account"
    CURRENCY_MISMATCH = "currency_mismatch"
    STATISTICAL_OUTLIER = "statistical_outlier"


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Sort order for presenting anomalies worst-first.
SEVERITY_ORDER = {
    Severity.HIGH.value: 0,
    Severity.MEDIUM.value: 1,
    Severity.LOW.value: 2,
}


class AnomalyStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    IGNORED = "IGNORED"


class TransactionStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REVERSED = "REVERSED"


class TransferState(StrEnum):
    """Mojaloop transfer states as defined by the FSPIOP API."""

    RECEIVED = "RECEIVED"
    RESERVED = "RESERVED"
    COMMITTED = "COMMITTED"
    ABORTED = "ABORTED"


class LedgerEntryStatus(StrEnum):
    UNRECONCILED = "UNRECONCILED"
    RECONCILED = "RECONCILED"
    PARTIALLY_RECONCILED = "PARTIALLY_RECONCILED"


class ERPStatus(StrEnum):
    OPEN = "OPEN"
    CLEARED = "CLEARED"
    WRITTEN_OFF = "WRITTEN_OFF"


class TransactionType(StrEnum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


class ForecastModel(StrEnum):
    PROPHET = "prophet"
    RIDGE = "ridge"
    SEASONAL_NAIVE = "seasonal_naive"


class SettlementWindowState(StrEnum):
    """Mojaloop settlement window lifecycle.

    Only ``OPEN`` still accepts transfers. Everything from ``CLOSED`` onwards is
    a stable boundary and therefore safe to reconcile against.
    """

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PENDING_SETTLEMENT = "PENDING_SETTLEMENT"
    SETTLED = "SETTLED"
    ABORTED = "ABORTED"
    ABORTING = "ABORTING"


class RunScope(StrEnum):
    """What a reconciliation run covered."""

    FULL = "full"
    SETTLEMENT_WINDOW = "settlement_window"
    SINGLE_TRANSACTION = "single_transaction"


__all__ = [
    "StrEnum",
    "MatchStatus",
    "ConfidenceTier",
    "LedgerType",
    "AnomalyType",
    "Severity",
    "SEVERITY_ORDER",
    "AnomalyStatus",
    "TransactionStatus",
    "TransferState",
    "LedgerEntryStatus",
    "ERPStatus",
    "TransactionType",
    "ForecastModel",
    "SettlementWindowState",
    "RunScope",
]
