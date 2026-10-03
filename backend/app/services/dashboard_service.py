"""Treasury dashboard aggregation.

The original implementation loaded every ``Reconciliation`` row and every
``Transaction`` row into Python objects on each dashboard request and counted
them with generator expressions. Only the anomaly count used a SQL aggregate.
It also summed ``tx.amount`` across mixed currencies, so INR and USD values were
added together to produce ``total_reconciled_value``.

This version pushes all counting and summing into SQL ``GROUP BY`` / ``SUM``,
sums the FX-normalised ``amount_base`` column so the headline value is
meaningful, and adds the breakdowns a treasury view actually needs: exposure per
currency, exception mix, ledger coverage, and the provenance of the last run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    AnomalyStatus,
    ERPStatus,
    LedgerEntryStatus,
    MatchStatus,
    SEVERITY_ORDER,
)
from app.models.bank_transaction import BankTransaction, ERPRecord
from app.models.reconciliation import (
    Anomaly,
    ManualMatch,
    Reconciliation,
    ReconciliationRun,
)
from app.models.transaction import Transaction

logger = logging.getLogger(__name__)


def _status_counts(db: Session) -> Dict[str, int]:
    """One GROUP BY instead of loading every reconciliation row."""
    rows = (
        db.query(Reconciliation.status, func.count(Reconciliation.id))
        .group_by(Reconciliation.status)
        .all()
    )
    return {str(status): int(count) for status, count in rows}


def _normalised_total(db: Session) -> float:
    """Sum of transaction value in the base currency.

    Falls back to the raw ``amount`` only for rows that have not been normalised
    yet, which keeps the figure correct during the first run after an upgrade.
    """
    total = db.query(
        func.sum(func.coalesce(Transaction.amount_base, Transaction.amount))
    ).scalar()
    return round(float(total or 0.0), 2)


def _currency_exposure(db: Session, limit: int = 10) -> List[Dict[str, Any]]:
    """Per-currency volume and value, so FX exposure is visible."""
    rows = (
        db.query(
            Transaction.currency,
            func.count(Transaction.id),
            func.sum(Transaction.amount),
            func.sum(func.coalesce(Transaction.amount_base, Transaction.amount)),
        )
        .group_by(Transaction.currency)
        .order_by(func.sum(func.coalesce(Transaction.amount_base, Transaction.amount)).desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "currency": currency or "UNKNOWN",
            "transactions": int(count or 0),
            "value_original": round(float(original or 0.0), 2),
            "value_base": round(float(base or 0.0), 2),
        }
        for currency, count, original, base in rows
    ]


def _anomaly_breakdown(db: Session) -> Dict[str, Any]:
    """Open exception mix by type and severity, with total value at risk."""
    by_type = (
        db.query(Anomaly.anomaly_type, func.count(Anomaly.id), func.avg(Anomaly.risk_score))
        .filter(Anomaly.status == AnomalyStatus.OPEN.value)
        .group_by(Anomaly.anomaly_type)
        .order_by(func.count(Anomaly.id).desc())
        .all()
    )
    by_severity = (
        db.query(Anomaly.severity, func.count(Anomaly.id))
        .filter(Anomaly.status == AnomalyStatus.OPEN.value)
        .group_by(Anomaly.severity)
        .all()
    )
    by_status = (
        db.query(Anomaly.status, func.count(Anomaly.id)).group_by(Anomaly.status).all()
    )

    severity_counts = {str(sev): int(count) for sev, count in by_severity}

    return {
        "by_type": [
            {
                "type": str(anomaly_type),
                "count": int(count or 0),
                "average_risk": round(float(avg_risk or 0.0), 3),
            }
            for anomaly_type, count, avg_risk in by_type
        ],
        "by_severity": dict(
            sorted(severity_counts.items(), key=lambda kv: SEVERITY_ORDER.get(kv[0], 9))
        ),
        "by_status": {str(status): int(count) for status, count in by_status},
    }


def _value_at_risk(db: Session) -> float:
    """Base-currency value of transactions carrying an open exception."""
    subquery = (
        db.query(Anomaly.transaction_id)
        .filter(Anomaly.status == AnomalyStatus.OPEN.value)
        .distinct()
        .subquery()
    )
    total = (
        db.query(func.sum(func.coalesce(Transaction.amount_base, Transaction.amount)))
        .filter(Transaction.transaction_id.in_(db.query(subquery.c.transaction_id)))
        .scalar()
    )
    return round(float(total or 0.0), 2)


def _ledger_coverage(db: Session) -> Dict[str, Any]:
    """How much of each ledger has been cleared, computed with SQL conditionals."""
    bank_total, bank_reconciled = db.query(
        func.count(BankTransaction.id),
        func.sum(
            case(
                (BankTransaction.status == LedgerEntryStatus.RECONCILED.value, 1),
                else_=0,
            )
        ),
    ).one()

    erp_total, erp_cleared = db.query(
        func.count(ERPRecord.id),
        func.sum(case((ERPRecord.status == ERPStatus.CLEARED.value, 1), else_=0)),
    ).one()

    bank_total = int(bank_total or 0)
    erp_total = int(erp_total or 0)
    bank_reconciled = int(bank_reconciled or 0)
    erp_cleared = int(erp_cleared or 0)

    return {
        "bank_records": bank_total,
        "bank_reconciled": bank_reconciled,
        "bank_open": bank_total - bank_reconciled,
        "bank_coverage_pct": round((bank_reconciled / bank_total * 100), 2) if bank_total else 0.0,
        "erp_records": erp_total,
        "erp_cleared": erp_cleared,
        "erp_open": erp_total - erp_cleared,
        "erp_coverage_pct": round((erp_cleared / erp_total * 100), 2) if erp_total else 0.0,
    }


def _last_run(db: Session) -> Optional[Dict[str, Any]]:
    """Provenance for the figures on screen."""
    run = (
        db.query(ReconciliationRun)
        .order_by(ReconciliationRun.started_at.desc(), ReconciliationRun.id.desc())
        .first()
    )
    if run is None:
        return None
    return {
        "run_id": run.id,
        "trigger": run.trigger,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "duration_seconds": run.duration_seconds,
        "transactions_examined": run.transactions_examined,
        "candidates_evaluated": run.candidates_evaluated,
        "manual_overrides_applied": run.manual_overrides_applied,
        "error_message": run.error_message,
    }


def _confidence_distribution(db: Session) -> Dict[str, int]:
    """Bucket match confidence so the quality of matching is visible."""
    buckets = (
        db.query(
            case(
                (Reconciliation.match_confidence >= 90, "90-100"),
                (Reconciliation.match_confidence >= 70, "70-89"),
                (Reconciliation.match_confidence >= 50, "50-69"),
                (Reconciliation.match_confidence > 0, "1-49"),
                else_="0",
            ).label("bucket"),
            func.count(Reconciliation.id),
        )
        .group_by("bucket")
        .all()
    )
    found = {str(bucket): int(count) for bucket, count in buckets}
    return {key: found.get(key, 0) for key in ("90-100", "70-89", "50-69", "1-49", "0")}


def get_dashboard_summary(db: Session) -> Dict[str, Any]:
    """Headline treasury metrics.

    The keys the existing frontend reads are preserved exactly; everything else
    is additive.
    """
    counts = _status_counts(db)
    matched = counts.get(MatchStatus.MATCHED.value, 0)
    partial = counts.get(MatchStatus.PARTIAL.value, 0)
    unmatched = counts.get(MatchStatus.UNMATCHED.value, 0)
    flagged = counts.get(MatchStatus.FLAGGED.value, 0)
    total_reconciliations = sum(counts.values())

    transaction_count = int(db.query(func.count(Transaction.id)).scalar() or 0)
    open_anomalies = int(
        db.query(func.count(Anomaly.id))
        .filter(Anomaly.status == AnomalyStatus.OPEN.value)
        .scalar()
        or 0
    )
    manual_matches = int(db.query(func.count(ManualMatch.id)).scalar() or 0)

    denominator = total_reconciliations or transaction_count
    match_rate = round((matched / denominator * 100), 2) if denominator else 0.0
    # Partial matches are genuine matches needing attention, not failures; a
    # treasury team tracks both numbers.
    settled_rate = (
        round(((matched + partial) / denominator * 100), 2) if denominator else 0.0
    )

    recent_cutoff = datetime.utcnow() - timedelta(hours=24)
    recent_transactions = int(
        db.query(func.count(Transaction.id))
        .filter(Transaction.created_at >= recent_cutoff)
        .scalar()
        or 0
    )

    base_currency = settings.fx.base_currency
    unnormalised = int(
        db.query(func.count(Transaction.id))
        .filter(Transaction.amount_base.is_(None))
        .scalar()
        or 0
    )

    summary: Dict[str, Any] = {
        # --- keys consumed by the existing dashboard ---
        "total_transactions": denominator,
        "matched_transactions": matched,
        "partial_matched": partial,
        "unmatched_transactions": unmatched,
        "match_rate": match_rate,
        "anomaly_count": open_anomalies,
        "total_reconciled_value": _normalised_total(db),
        # --- additions ---
        "flagged_for_review": flagged,
        "settled_rate": settled_rate,
        "base_currency": base_currency,
        "value_at_risk": _value_at_risk(db),
        "manual_matches": manual_matches,
        "transactions_last_24h": recent_transactions,
        "unnormalised_transactions": unnormalised,
        "currency_exposure": _currency_exposure(db),
        "anomalies": _anomaly_breakdown(db),
        "ledger_coverage": _ledger_coverage(db),
        "confidence_distribution": _confidence_distribution(db),
        "last_run": _last_run(db),
    }

    notice_parts = [
        f"Values are normalised to {base_currency} using the stored FX rate table."
    ]
    seeded = int(
        db.query(func.count(Transaction.id)).filter(Transaction.source == "seed").scalar()
        or 0
    )
    if seeded:
        notice_parts.append(
            f"{seeded} of {transaction_count} transactions are synthetic demo records."
        )
    if unnormalised:
        notice_parts.append(
            f"{unnormalised} transaction(s) are not yet FX-normalised and are counted at "
            f"face value."
        )
    summary["synthetic_data_notice"] = " ".join(notice_parts)

    return summary


def get_reconciliation_runs(db: Session, limit: int = 20) -> List[Dict[str, Any]]:
    """Run history, for auditability and for comparing runs."""
    runs = (
        db.query(ReconciliationRun)
        .order_by(ReconciliationRun.started_at.desc(), ReconciliationRun.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "run_id": run.id,
            "trigger": run.trigger,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "duration_seconds": run.duration_seconds,
            "transactions_examined": run.transactions_examined,
            "candidates_evaluated": run.candidates_evaluated,
            "matched_count": run.matched_count,
            "partial_count": run.partial_count,
            "unmatched_count": run.unmatched_count,
            "flagged_count": run.flagged_count,
            "anomaly_count": run.anomaly_count,
            "manual_overrides_applied": run.manual_overrides_applied,
            "base_currency": run.base_currency,
            "error_message": run.error_message,
        }
        for run in runs
    ]


__all__ = ["get_dashboard_summary", "get_reconciliation_runs"]
