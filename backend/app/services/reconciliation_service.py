"""Reconciliation orchestration.

Responsibilities split out of the original single 370-line module:

* candidate generation + scoring now live in ``matching_engine``
* anomaly detection now lives in ``anomaly_service``
* this module owns the *transaction script*: load, normalise, match, detect,
  persist, and record the run

Behavioural fixes relative to the original ``run_reconciliation``:

1. It no longer deletes the entire ``reconciliations`` and ``anomalies`` tables
   and commits that deletion before producing replacements. A crash mid-run
   previously left the system with no results at all.
2. Resolved and ignored anomalies survive a re-run instead of being deleted and
   silently re-raised.
3. Manual matches survive a re-run. They are stored in ``manual_matches`` and
   re-applied after automatic matching, rather than being written into
   ``reconciliations`` where the next run's DELETE destroyed them.
4. Bank and ERP rows get their ``status`` updated when they are consumed by a
   match. Previously every ledger row stayed ``UNRECONCILED`` forever.
5. Normalised base-currency amounts are persisted once per run instead of FX
   being recomputed inside the matching loops.
6. A webhook no longer triggers a full re-reconciliation of the whole database;
   ``reconcile_transaction`` matches just the affected transfer.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    AnomalyStatus,
    ConfidenceTier,
    ERPStatus,
    LedgerEntryStatus,
    LedgerType,
    MatchStatus,
    RunScope,
    TransactionStatus,
    TransferState,
)
from app.models.bank_transaction import BankTransaction, ERPRecord
from app.models.reconciliation import (
    Anomaly,
    CashFlowForecast,
    ManualMatch,
    Reconciliation,
    ReconciliationRun,
)
from app.models.transaction import Transaction
from app.services.anomaly_service import DetectedAnomaly, detect_all
from app.services.fx_service import FXConverter, get_converter, init_fx_rates
from app.services.matching_engine import MatchDecision, MatchingEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def normalize_amounts(db: Session, converter: Optional[FXConverter] = None) -> Dict[str, int]:
    """Persist base-currency amounts for every source row that lacks one.

    This is the "unified schema" step: after it runs, all three ledgers carry a
    comparable ``amount_base`` plus the rate used to produce it, so matching and
    dashboard aggregation no longer need to convert on the fly.
    """
    converter = converter or get_converter(db)
    base = converter.base_currency
    now = datetime.utcnow()
    counts = {"transactions": 0, "bank": 0, "erp": 0}

    tx_updates: List[Dict[str, object]] = []
    for row in db.query(Transaction).filter(Transaction.amount_base.is_(None)).all():
        result = converter.convert(row.amount, row.currency, base)
        tx_updates.append(
            {
                "id": row.id,
                "amount_base": result.amount,
                "base_currency": base,
                "fx_rate_used": result.rate,
                "normalized_at": now,
            }
        )
    if tx_updates:
        db.bulk_update_mappings(Transaction, tx_updates)
        counts["transactions"] = len(tx_updates)

    bank_updates: List[Dict[str, object]] = []
    for row in db.query(BankTransaction).filter(BankTransaction.amount_base.is_(None)).all():
        result = converter.convert(row.amount, row.currency, base)
        bank_updates.append(
            {
                "id": row.id,
                "amount_base": result.amount,
                "base_currency": base,
                "fx_rate_used": result.rate,
                "normalized_at": now,
            }
        )
    if bank_updates:
        db.bulk_update_mappings(BankTransaction, bank_updates)
        counts["bank"] = len(bank_updates)

    erp_updates: List[Dict[str, object]] = []
    for row in db.query(ERPRecord).filter(ERPRecord.amount_base.is_(None)).all():
        result = converter.convert(row.expected_amount, row.currency, base)
        erp_updates.append(
            {
                "id": row.id,
                "amount_base": result.amount,
                "base_currency": base,
                "fx_rate_used": result.rate,
                "normalized_at": now,
            }
        )
    if erp_updates:
        db.bulk_update_mappings(ERPRecord, erp_updates)
        counts["erp"] = len(erp_updates)

    if any(counts.values()):
        db.commit()
        logger.info(
            "Normalised amounts to %s: %d transactions, %d bank rows, %d ERP rows",
            base,
            counts["transactions"],
            counts["bank"],
            counts["erp"],
        )
    return counts


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _expunge_stale(db: Session, model_types: tuple) -> int:
    """Detach already-loaded instances of the given models from the session.

    Needed after a bulk ``delete(synchronize_session=False)``: the ORM does not
    know those rows are gone, so their identities linger and conflict with
    replacement rows that reuse the same primary keys.
    """
    removed = 0
    for instance in list(db.identity_map.values()):
        if isinstance(instance, model_types):
            db.expunge(instance)
            removed += 1
    return removed


def _build_reconciliation_row(
    decision: MatchDecision, base_currency: str, tx_amount_base: float
) -> Reconciliation:
    score = decision.score
    candidate = decision.candidate

    return Reconciliation(
        transaction_id=decision.transaction.transaction_id,
        ledger_id=candidate.ledger_id if candidate else None,
        ledger_type=candidate.ledger_type if candidate else LedgerType.NONE.value,
        status=decision.status,
        amount_difference=round(score.amount_delta_base, 2) if score else 0.0,
        match_confidence=score.total if score else 0.0,
        confidence_tier=decision.tier,
        remarks=decision.remarks,
        reconciled_at=datetime.utcnow(),
        score_amount=score.amount if score else 0.0,
        score_name=score.name if score else 0.0,
        score_reference=score.reference if score else 0.0,
        score_date=score.date if score else 0.0,
        base_currency=base_currency,
        transaction_amount_base=tx_amount_base,
        ledger_amount_base=candidate.amount_base if candidate else None,
        date_difference_days=score.date_delta_days if score else None,
        is_manual=0,
    )


def _persist_anomalies(
    db: Session, findings: Sequence[DetectedAnomaly], run_id: Optional[int]
) -> int:
    """Insert findings, suppressing any a human has already dealt with.

    An anomaly whose ``(transaction_id, anomaly_type)`` is already RESOLVED or
    IGNORED is not re-created, so triage work is not undone by the next run.
    """
    if not findings:
        return 0

    handled = {
        (row.transaction_id, row.anomaly_type)
        for row in db.query(Anomaly.transaction_id, Anomaly.anomaly_type)
        .filter(Anomaly.status.in_([AnomalyStatus.RESOLVED.value, AnomalyStatus.IGNORED.value]))
        .all()
    }

    now = datetime.utcnow()
    inserted = 0
    for finding in findings:
        if finding.key() in handled:
            continue
        db.add(
            Anomaly(
                transaction_id=finding.transaction_id,
                anomaly_type=finding.anomaly_type,
                severity=finding.severity,
                risk_score=finding.risk_score,
                explanation=finding.explanation,
                detected_at=now,
                status=AnomalyStatus.OPEN.value,
                run_id=run_id,
                detector=finding.detector,
            )
        )
        inserted += 1
    return inserted


def _update_ledger_statuses(
    db: Session, decisions: Sequence[MatchDecision], reset_all: bool = True
) -> Tuple[int, int]:
    """Mark consumed bank/ERP rows as reconciled.

    The ``status`` columns on both ledgers previously never changed from their
    defaults, so there was no way to tell which ledger lines were still open.

    ``reset_all`` returns every ledger row to its open state first, which is
    correct for a full run (a row released by a re-run must go back to open).
    A scoped run passes ``False``: it only knows about its own slice, so
    resetting everything would mark other windows' matched ledger lines as open
    again.
    """
    matched_bank: List[str] = []
    partial_bank: List[str] = []
    matched_erp: List[str] = []
    partial_erp: List[str] = []

    for decision in decisions:
        candidate = decision.candidate
        if candidate is None:
            continue
        if decision.status == MatchStatus.MATCHED.value:
            (matched_bank if candidate.ledger_type == LedgerType.BANK.value else matched_erp).append(
                candidate.ledger_id
            )
        elif decision.status == MatchStatus.PARTIAL.value:
            (partial_bank if candidate.ledger_type == LedgerType.BANK.value else partial_erp).append(
                candidate.ledger_id
            )

    bank_updated = 0
    erp_updated = 0

    if reset_all:
        # Reset first so a row released by a re-run returns to UNRECONCILED.
        db.query(BankTransaction).filter(
            BankTransaction.status != LedgerEntryStatus.UNRECONCILED.value
        ).update(
            {BankTransaction.status: LedgerEntryStatus.UNRECONCILED.value},
            synchronize_session=False,
        )
        db.query(ERPRecord).filter(ERPRecord.status != ERPStatus.OPEN.value).update(
            {ERPRecord.status: ERPStatus.OPEN.value}, synchronize_session=False
        )

    for ids, status in (
        (matched_bank, LedgerEntryStatus.RECONCILED.value),
        (partial_bank, LedgerEntryStatus.PARTIALLY_RECONCILED.value),
    ):
        if ids:
            bank_updated += (
                db.query(BankTransaction)
                .filter(BankTransaction.bank_statement_id.in_(ids))
                .update({BankTransaction.status: status}, synchronize_session=False)
            )

    if matched_erp:
        erp_updated += (
            db.query(ERPRecord)
            .filter(ERPRecord.erp_id.in_(matched_erp))
            .update({ERPRecord.status: ERPStatus.CLEARED.value}, synchronize_session=False)
        )
    if partial_erp:
        erp_updated += (
            db.query(ERPRecord)
            .filter(ERPRecord.erp_id.in_(partial_erp))
            .update({ERPRecord.status: ERPStatus.OPEN.value}, synchronize_session=False)
        )

    return bank_updated, erp_updated


def apply_manual_overrides(db: Session, run_id: Optional[int] = None) -> int:
    """Re-apply every active human override on top of the engine's output."""
    overrides = db.query(ManualMatch).filter(ManualMatch.is_active == 1).all()
    if not overrides:
        return 0

    applied = 0
    for override in overrides:
        row = (
            db.query(Reconciliation)
            .filter(Reconciliation.transaction_id == override.transaction_id)
            .order_by(Reconciliation.id.desc())
            .first()
        )
        remark = (
            override.remarks
            or f"Manually matched by {override.created_by} on "
            f"{override.created_at:%Y-%m-%d %H:%M} UTC."
        )

        if row is None:
            row = Reconciliation(transaction_id=override.transaction_id)
            db.add(row)

        row.ledger_id = override.ledger_id
        row.ledger_type = override.ledger_type
        row.status = MatchStatus.MATCHED.value
        row.match_confidence = 100.0
        row.confidence_tier = ConfidenceTier.HIGH.value
        row.remarks = f"[manual override] {remark}"
        row.is_manual = 1
        row.reconciled_at = datetime.utcnow()
        applied += 1

        # A human-confirmed match closes any open exception on that transfer.
        open_anomalies = (
            db.query(Anomaly)
            .filter(
                Anomaly.transaction_id == override.transaction_id,
                Anomaly.status == AnomalyStatus.OPEN.value,
            )
            .all()
        )
        for anomaly in open_anomalies:
            anomaly.status = AnomalyStatus.RESOLVED.value
            anomaly.resolved_at = datetime.utcnow()
            anomaly.resolution_note = "Closed automatically by manual match override."

    logger.info("Re-applied %d manual match override(s)", applied)
    return applied


# ---------------------------------------------------------------------------
# Full reconciliation run
# ---------------------------------------------------------------------------


def run_reconciliation(
    db: Session,
    trigger: str = "manual",
    enable_statistical: bool = True,
    refresh_forecast: bool = True,
    scope: str = RunScope.FULL.value,
    settlement_window_id: Optional[str] = None,
    scope_from: Optional[datetime] = None,
    scope_to: Optional[datetime] = None,
) -> Dict[str, object]:
    """Reconcile transactions against the bank and ERP ledgers.

    By default this covers every transaction. Passing ``scope_from``/``scope_to``
    (normally derived from a settlement window) restricts the run to transactions
    created inside that interval, and — importantly — only replaces the
    reconciliation rows and open anomalies belonging to those transactions. A
    scoped run must not discard results for transactions outside its scope, or
    reconciling one window would erase another window's work.

    Returns the same keys the previous implementation returned, plus run
    metadata and matching statistics.
    """
    started = datetime.utcnow()
    config = settings.matching
    is_scoped = scope_from is not None or scope_to is not None

    run = ReconciliationRun(
        started_at=started,
        status="running",
        trigger=trigger,
        base_currency=settings.fx.base_currency,
        scope=scope,
        settlement_window_id=settlement_window_id,
        scope_from=scope_from,
        scope_to=scope_to,
        parameters_json=json.dumps(
            {
                "scope": scope,
                "settlement_window_id": settlement_window_id,
                "scope_from": scope_from.isoformat() if scope_from else None,
                "scope_to": scope_to.isoformat() if scope_to else None,
                "matched_threshold": config.matched_threshold,
                "partial_threshold": config.partial_threshold,
                "candidate_floor": config.candidate_floor,
                "amount_relative_tolerance": config.amount_relative_tolerance,
                "amount_absolute_tolerance": config.amount_absolute_tolerance,
                "date_tolerance_days": config.date_tolerance_days,
                "date_window_days": config.date_window_days,
                "weights": {
                    "amount": config.weight_amount,
                    "name": config.weight_name,
                    "reference": config.weight_reference,
                    "date": config.weight_date,
                },
                "statistical_detection": enable_statistical,
            }
        ),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        converter = get_converter(db)
        normalize_amounts(db, converter)

        transaction_query = db.query(Transaction)
        if scope_from is not None:
            transaction_query = transaction_query.filter(Transaction.created_at >= scope_from)
        if scope_to is not None:
            transaction_query = transaction_query.filter(Transaction.created_at <= scope_to)
        transactions = transaction_query.order_by(Transaction.id).all()

        # The ledger side is intentionally not narrowed to the scope window:
        # settlement legitimately lags the transfer, so a transfer inside the
        # window can match a bank line dated outside it.
        bank_records = db.query(BankTransaction).order_by(BankTransaction.id).all()
        erp_records = db.query(ERPRecord).order_by(ERPRecord.id).all()

        if is_scoped and not transactions:
            finished = datetime.utcnow()
            run.finished_at = finished
            run.status = "success"
            run.duration_seconds = round((finished - started).total_seconds(), 3)
            db.commit()
            return {
                "status": "success",
                "run_id": run.id,
                "scope": scope,
                "settlement_window_id": settlement_window_id,
                "total_reconciled": 0,
                "matched_count": 0,
                "partial_count": 0,
                "unmatched_count": 0,
                "flagged_count": 0,
                "anomalies_detected": 0,
                "manual_overrides_applied": 0,
                "duration_seconds": run.duration_seconds,
                "base_currency": converter.base_currency,
                "unresolved_currencies": [],
                "matching_stats": {},
                "message": "No transactions fall inside the requested scope.",
            }

        engine = MatchingEngine(converter, config)
        decisions, best_name_similarity, _index = engine.run(
            transactions, bank_records, erp_records
        )

        amount_base_by_id = {
            decision.transaction.transaction_id: engine.transaction_amount_base(
                decision.transaction
            )
            for decision in decisions
        }

        findings = detect_all(
            decisions,
            amount_base_by_id,
            best_name_similarity,
            enable_statistical=enable_statistical,
        )

        # Replace prior automatic results. Open anomalies from earlier runs are
        # cleared because they are about to be recomputed; resolved and ignored
        # ones are retained.
        #
        # A scoped run deletes only the rows belonging to the transactions it is
        # about to re-evaluate. Deleting the whole table here would make
        # reconciling one settlement window destroy every other window's result.
        if is_scoped:
            scoped_ids = [tx.transaction_id for tx in transactions]
            for chunk_start in range(0, len(scoped_ids), 500):
                chunk = scoped_ids[chunk_start : chunk_start + 500]
                db.query(Reconciliation).filter(
                    Reconciliation.transaction_id.in_(chunk)
                ).delete(synchronize_session=False)
                db.query(Anomaly).filter(
                    Anomaly.transaction_id.in_(chunk),
                    Anomaly.status == AnomalyStatus.OPEN.value,
                ).delete(synchronize_session=False)
        else:
            db.query(Reconciliation).delete(synchronize_session=False)
            db.query(Anomaly).filter(Anomaly.status == AnomalyStatus.OPEN.value).delete(
                synchronize_session=False
            )

        # A bulk delete leaves the deleted rows in the session's identity map.
        # SQLite reuses primary keys after a delete, so the replacement rows
        # flush with the same ids and collide with those stale identities. Drop
        # them explicitly; they are not needed again in this run.
        _expunge_stale(db, (Reconciliation, Anomaly))

        base = converter.base_currency
        matched = partial = unmatched = flagged = 0

        for decision in decisions:
            tx_amount_base = amount_base_by_id.get(decision.transaction.transaction_id, 0.0)
            db.add(_build_reconciliation_row(decision, base, tx_amount_base))

            if decision.status == MatchStatus.MATCHED.value:
                matched += 1
            elif decision.status == MatchStatus.PARTIAL.value:
                partial += 1
            elif decision.status == MatchStatus.FLAGGED.value:
                flagged += 1
            else:
                unmatched += 1

        anomaly_count = _persist_anomalies(db, findings, run.id)
        _update_ledger_statuses(db, decisions, reset_all=not is_scoped)

        # Flush the new reconciliation rows before the override pass queries
        # them, so the override reads the current unit of work rather than
        # colliding with it in the identity map.
        db.flush()
        overrides = apply_manual_overrides(db, run.id)

        finished = datetime.utcnow()
        run.finished_at = finished
        run.status = "success"
        run.transactions_examined = len(transactions)
        run.candidates_evaluated = engine.stats.pairs_scored
        run.matched_count = matched
        run.partial_count = partial
        run.unmatched_count = unmatched
        run.flagged_count = flagged
        run.anomaly_count = anomaly_count
        run.manual_overrides_applied = overrides
        run.duration_seconds = round((finished - started).total_seconds(), 3)

        db.commit()

        if refresh_forecast:
            # Imported here to avoid a circular import at module load.
            from app.services.forecasting_service import generate_forecast

            try:
                generate_forecast(db, days=settings.forecast.default_horizon_days)
            except Exception:
                logger.exception("Forecast refresh failed after reconciliation run %s", run.id)

        unknown = converter.unknown_currencies
        if unknown:
            logger.warning(
                "Run %s encountered currencies with no FX rate: %s",
                run.id,
                ", ".join(unknown),
            )

        total = len(transactions)
        return {
            "status": "success",
            "run_id": run.id,
            "scope": scope,
            "settlement_window_id": settlement_window_id,
            "total_reconciled": total,
            "matched_count": matched,
            "partial_count": partial,
            "unmatched_count": unmatched,
            "flagged_count": flagged,
            "anomalies_detected": anomaly_count,
            "manual_overrides_applied": overrides,
            "duration_seconds": run.duration_seconds,
            "base_currency": base,
            "unresolved_currencies": unknown,
            "matching_stats": engine.stats.as_dict(),
            "message": (
                f"Reconciliation complete. {matched}/{total} matched, {partial} partial, "
                f"{flagged} held for review, {unmatched} unmatched, "
                f"{anomaly_count} anomalies flagged."
            ),
        }

    except Exception as exc:
        db.rollback()
        run.status = "failed"
        run.finished_at = datetime.utcnow()
        run.error_message = str(exc)[:2000]
        run.duration_seconds = round((datetime.utcnow() - started).total_seconds(), 3)
        db.commit()
        logger.exception("Reconciliation run %s failed", run.id)
        raise


# ---------------------------------------------------------------------------
# Incremental reconciliation (single transaction)
# ---------------------------------------------------------------------------


def reconcile_transaction(
    db: Session, transaction: Transaction, converter: Optional[FXConverter] = None
) -> Dict[str, object]:
    """Reconcile one transaction without touching the rest of the database.

    The webhook path previously called ``run_reconciliation`` for every inbound
    transfer, re-matching the entire database and retraining the forecast model
    on each message. This narrows the ledger scan to rows that could plausibly
    match, using the reference index and an amount window on the persisted
    ``amount_base`` column.
    """
    converter = converter or get_converter(db)
    config = settings.matching
    base = converter.base_currency

    # Ensure the transaction itself is normalised.
    if transaction.amount_base is None:
        result = converter.convert(transaction.amount, transaction.currency, base)
        transaction.amount_base = result.amount
        transaction.base_currency = base
        transaction.fx_rate_used = result.rate
        transaction.normalized_at = datetime.utcnow()
        db.commit()

    tx_amount_base = float(transaction.amount_base or 0.0)
    tolerance = max(
        config.amount_absolute_tolerance,
        abs(tx_amount_base) * config.amount_relative_tolerance,
    )
    window = tolerance * 5.0
    low, high = tx_amount_base - window, tx_amount_base + window
    reference = (transaction.reference_id or "").strip()

    bank_query = db.query(BankTransaction).filter(
        or_(
            BankTransaction.amount_base.between(low, high),
            BankTransaction.amount_base.is_(None),
            BankTransaction.reference_number == reference if reference else False,
        )
    )
    erp_query = db.query(ERPRecord).filter(
        or_(
            ERPRecord.amount_base.between(low, high),
            ERPRecord.amount_base.is_(None),
            ERPRecord.invoice_number == reference if reference else False,
        )
    )

    bank_records = bank_query.limit(settings.max_page_size).all()
    erp_records = erp_query.limit(settings.max_page_size).all()

    engine = MatchingEngine(converter, config)
    decisions, best_name_similarity, _index = engine.run(
        [transaction], bank_records, erp_records
    )
    decision = decisions[0]

    existing = (
        db.query(Reconciliation)
        .filter(Reconciliation.transaction_id == transaction.transaction_id)
        .order_by(Reconciliation.id.desc())
        .first()
    )
    if existing is not None and existing.is_manual == 1:
        logger.info(
            "Skipping automatic result for %s: a manual override is in force",
            transaction.transaction_id,
        )
        return {
            "transaction_id": transaction.transaction_id,
            "status": existing.status,
            "match_confidence": existing.match_confidence,
            "ledger_id": existing.ledger_id,
            "skipped": "manual override in force",
        }

    row = _build_reconciliation_row(decision, base, tx_amount_base)
    if existing is not None:
        db.delete(existing)
    db.add(row)

    findings = detect_all(
        [decision],
        {transaction.transaction_id: tx_amount_base},
        best_name_similarity,
        enable_statistical=False,  # a single sample cannot support a population model
    )
    db.query(Anomaly).filter(
        Anomaly.transaction_id == transaction.transaction_id,
        Anomaly.status == AnomalyStatus.OPEN.value,
    ).delete(synchronize_session=False)
    anomaly_count = _persist_anomalies(db, findings, None)

    _update_single_ledger_status(db, decision)
    db.commit()

    return {
        "transaction_id": transaction.transaction_id,
        "status": decision.status,
        "confidence_tier": decision.tier,
        "match_confidence": decision.score.total if decision.score else 0.0,
        "ledger_id": decision.candidate.ledger_id if decision.candidate else None,
        "ledger_type": decision.candidate.ledger_type if decision.candidate else None,
        "remarks": decision.remarks,
        "anomalies_detected": anomaly_count,
        "candidates_evaluated": engine.stats.pairs_scored,
    }


def _update_single_ledger_status(db: Session, decision: MatchDecision) -> None:
    candidate = decision.candidate
    if candidate is None or decision.status not in {
        MatchStatus.MATCHED.value,
        MatchStatus.PARTIAL.value,
    }:
        return

    if candidate.ledger_type == LedgerType.BANK.value:
        status = (
            LedgerEntryStatus.RECONCILED.value
            if decision.status == MatchStatus.MATCHED.value
            else LedgerEntryStatus.PARTIALLY_RECONCILED.value
        )
        db.query(BankTransaction).filter(
            BankTransaction.bank_statement_id == candidate.ledger_id
        ).update({BankTransaction.status: status}, synchronize_session=False)
    else:
        if decision.status == MatchStatus.MATCHED.value:
            db.query(ERPRecord).filter(ERPRecord.erp_id == candidate.ledger_id).update(
                {ERPRecord.status: ERPStatus.CLEARED.value}, synchronize_session=False
            )


# ---------------------------------------------------------------------------
# Mojaloop transfer intake
# ---------------------------------------------------------------------------


def process_transfer(db: Session, transfer) -> Dict[str, object]:
    """Persist an inbound Mojaloop transfer and reconcile just that transfer.

    Idempotent: replaying the same ``transactionId`` updates the existing record
    rather than creating a duplicate or raising.
    """
    transaction = (
        db.query(Transaction)
        .filter(Transaction.transaction_id == transfer.transactionId)
        .first()
    )
    created = False

    if transaction is None:
        transaction = Transaction(
            transaction_id=transfer.transactionId,
            reference_id=getattr(transfer, "referenceId", None)
            or f"REF-{uuid.uuid4().hex[:6].upper()}",
            payer=transfer.payer,
            payee=transfer.payee,
            payer_fsp=transfer.payerFsp,
            payee_fsp=transfer.payeeFsp,
            amount=transfer.amount,
            currency=getattr(transfer, "currency", settings.fx.base_currency),
            status=TransactionStatus.SUCCESS.value,
            transfer_state=getattr(transfer, "transferState", TransferState.COMMITTED.value),
            created_at=datetime.utcnow(),
            source="mojaloop",
            idempotency_key=transfer.transactionId,
            raw_payload=_safe_payload(transfer),
            ingested_at=datetime.utcnow(),
        )
        db.add(transaction)
        created = True
    else:
        # Replay or state update: advance the state, do not duplicate.
        transaction.status = TransactionStatus.SUCCESS.value
        transaction.transfer_state = getattr(
            transfer, "transferState", transaction.transfer_state
        )
        transaction.raw_payload = _safe_payload(transfer)

    db.commit()
    db.refresh(transaction)

    outcome = reconcile_transaction(db, transaction)

    return {
        "transaction_id": transaction.transaction_id,
        "status": transaction.status,
        "transfer_state": transaction.transfer_state,
        "created": created,
        "reconciliation": outcome,
        "message": (
            "Transfer recorded and reconciled incrementally."
            if created
            else "Transfer already known; state updated and re-reconciled."
        ),
    }


def _safe_payload(transfer) -> Optional[str]:
    """Serialise an inbound payload for audit without failing the request."""
    try:
        if hasattr(transfer, "model_dump"):
            return json.dumps(transfer.model_dump(), default=str)
        if isinstance(transfer, dict):
            return json.dumps(transfer, default=str)
        return json.dumps(vars(transfer), default=str)
    except Exception:  # pragma: no cover - audit data is best effort
        return None


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------

PAYERS = [
    "Acme Corporation", "Apex Retailers", "Nairobi Enterprises", "Euro Import Co",
    "Oceanic Shipping", "Delta Innovations", "Sun Energy Ltd", "FastPay Merchant",
    "Zenith Solutions", "Nexus Logistics", "Horizon Traders", "Orion Systems",
    "Sterling Pharma", "Vanguard Energy", "Global Telecom", "Atlas Manufacturing",
    "Cobalt Dynamics",
]

PAYEES = [
    "Global Logistics Ltd", "TechSupply Pvt Ltd", "Safari Ventures",
    "Berlin Distro GmbH", "Port Terminal Services", "Omega Softwares",
    "Grid Power Corp", "Supermarket Retail", "CloudNet Services",
    "Freightways Global", "Apex Wholesale", "Quantum IT", "Premier Supplies",
    "Eco Power Systems", "InterState Connect", "SteelWorks Inc",
]

FSP_LIST = ["BankA", "BankB", "MobileMoneyX", "PayCentral", "DFSP-Alpha"]
CURRENCIES = ["USD", "INR", "EUR", "KES"]

_AMOUNT_RANGES = {
    "USD": (2_000.0, 85_000.0),
    "INR": (150_000.0, 2_500_000.0),
    "EUR": (5_000.0, 60_000.0),
    "KES": (50_000.0, 800_000.0),
}


def seed_demo_data(
    db: Session,
    count: int = 150,
    seed: Optional[int] = None,
    reconcile: bool = True,
    history_days: int = 90,
) -> Dict[str, object]:
    """Generate a synthetic but realistic dataset and reconcile it.

    Improvements over the original generator:

    * ``seed`` makes the dataset reproducible, which the random generator
      previously prevented.
    * Bank and ERP rows get realistic settlement lag instead of defaulting to
      ``utcnow``, so date tolerance and ``timing_anomaly`` can actually be
      exercised.
    * Deliberate edge cases are injected: a late settlement, a name variant that
      only fuzzy matching can resolve, a reference-only match, and a missing
      bank row.
    * Activity is spread across ``history_days`` with weekday seasonality
      instead of being compressed into a 72-hour window. The original generator
      produced only ~3 days of history, which is why the forecasting service had
      to invent 30 days of synthetic data to have anything to fit.
    """
    rng = random.Random(seed)

    db.query(Reconciliation).delete(synchronize_session=False)
    db.query(Anomaly).delete(synchronize_session=False)
    db.query(CashFlowForecast).delete(synchronize_session=False)
    db.query(ManualMatch).delete(synchronize_session=False)
    db.query(Transaction).delete(synchronize_session=False)
    db.query(BankTransaction).delete(synchronize_session=False)
    db.query(ERPRecord).delete(synchronize_session=False)
    db.commit()

    init_fx_rates(db)
    batch_id = f"seed-{uuid.uuid4().hex[:8]}"
    now = datetime.utcnow()

    # --- deliberate, documented edge cases -----------------------------
    base_txs = [
        ("TX-1001", "REF-8801", "Acme Corporation", "Global Logistics Ltd", 15400.0, "USD", "BankA", "BankB"),
        ("TX-1002", "REF-8802", "Apex Retailers", "TechSupply Pvt Ltd", 450000.0, "INR", "MobileMoneyX", "BankA"),
        ("TX-1003", "REF-8803", "Nairobi Enterprises", "Safari Ventures", 125000.0, "KES", "DFSP-Alpha", "PayCentral"),
        ("TX-1004", "REF-8804", "Euro Import Co", "Berlin Distro GmbH", 28900.0, "EUR", "BankB", "BankA"),
        # Duplicate of TX-1001: same payer, payee, amount and currency.
        ("TX-1005", "REF-8805", "Acme Corporation", "Global Logistics Ltd", 15400.0, "USD", "BankA", "BankB"),
        ("TX-1006", "REF-8806", "Oceanic Shipping", "Port Terminal Services", 72300.0, "USD", "BankA", "DFSP-Alpha"),
        ("TX-1007", "REF-8807", "Delta Innovations", "Omega Softwares", 18500.0, "USD", "PayCentral", "BankB"),
        ("TX-1008", "REF-8808", "Sun Energy Ltd", "Grid Power Corp", 940000.0, "INR", "MobileMoneyX", "PayCentral"),
        # No bank or ERP counterpart at all: missing settlement + unknown account.
        ("TX-1009", "REF-8809", "Unrecognized Sender LLC", "Mystery Account Inc", 65000.0, "USD", "BankB", "DFSP-Alpha"),
        ("TX-1010", "REF-8810", "FastPay Merchant", "Supermarket Retail", 8400.0, "USD", "PayCentral", "BankA"),
    ]

    for tx_id, ref_id, payer, payee, amount, currency, payer_fsp, payee_fsp in base_txs:
        db.add(
            Transaction(
                transaction_id=tx_id,
                reference_id=ref_id,
                payer=payer,
                payee=payee,
                payer_fsp=payer_fsp,
                payee_fsp=payee_fsp,
                amount=amount,
                currency=currency,
                status=TransactionStatus.SUCCESS.value,
                transfer_state=TransferState.COMMITTED.value,
                created_at=now - timedelta(hours=rng.randint(1, 48)),
                source="seed",
                source_batch_id=batch_id,
            )
        )

    # Bank rows. Note the name variants: these only resolve via fuzzy matching.
    bank_txs = [
        ("BS-9001", "REF-8801", "Acme Corp", 15400.0, "USD", 1),
        ("BS-9002", "REF-8802", "Apex Retailers Ltd", 450000.0, "INR", 1),
        ("BS-9003", "REF-8803", "Nairobi Ent", 125000.0, "KES", 2),
        # Amount differs by 400 EUR -> partial match + amount_mismatch anomaly.
        ("BS-9004", "REF-8804", "Euro Import Co", 28500.0, "EUR", 1),
        ("BS-9006", "REF-8806", "Oceanic Shipping", 72300.0, "USD", 1),
        ("BS-9007", "REF-8807", "Delta Innovations", 18500.0, "USD", 1),
        # Settles 9 days late -> timing_anomaly.
        ("BS-9008", "REF-8808", "Sun Energy Limited", 940000.0, "INR", 9),
        ("BS-9010", "REF-8810", "FastPay Merchant", 8400.0, "USD", 1),
    ]

    for bank_id, reference, counterparty, amount, currency, lag_days in bank_txs:
        db.add(
            BankTransaction(
                bank_statement_id=bank_id,
                account_number="ACC-99042",
                counterparty=counterparty,
                amount=amount,
                currency=currency,
                transaction_type="CREDIT",
                reference_number=reference,
                value_date=now - timedelta(hours=rng.randint(1, 24)) + timedelta(days=lag_days),
                bank_name="Central Settlement Bank",
                source="seed",
                source_batch_id=batch_id,
            )
        )

    erp_recs = [
        ("ERP-5001", "REF-8801", "Global Logistics Ltd", 15400.0, "USD"),
        ("ERP-5002", "REF-8802", "TechSupply Pvt Ltd", 450000.0, "INR"),
        ("ERP-5003", "REF-8803", "Safari Ventures", 125000.0, "KES"),
        ("ERP-5004", "REF-8804", "Berlin Distro GmbH", 28900.0, "EUR"),
    ]

    for erp_id, invoice, name, amount, currency in erp_recs:
        db.add(
            ERPRecord(
                erp_id=erp_id,
                invoice_number=invoice,
                customer_vendor_name=name,
                expected_amount=amount,
                currency=currency,
                posting_date=now - timedelta(days=rng.randint(0, 3)),
                ledger_account="1100-ACCOUNTS-RECEIVABLE",
                source="seed",
                source_batch_id=batch_id,
            )
        )

    # --- bulk generation ------------------------------------------------
    span_days = max(1, history_days)
    for index in range(11, count + 1):
        tx_id = f"TX-{1000 + index}"
        reference = f"REF-{8800 + index}"
        payer = rng.choice(PAYERS)
        payee = rng.choice(PAYEES)
        currency = rng.choice(CURRENCIES)
        low, high = _AMOUNT_RANGES[currency]
        amount = round(rng.uniform(low, high), 2)

        # Spread activity across the history window, with weekday seasonality
        # (weekends quieter) so the forecasting model has a real signal to fit.
        days_ago = rng.randint(0, span_days - 1)
        candidate_day = now - timedelta(days=days_ago)
        if candidate_day.weekday() >= 5 and rng.random() < 0.65:
            # Push most weekend activity onto the following Monday.
            candidate_day += timedelta(days=7 - candidate_day.weekday())
            if candidate_day > now:
                candidate_day = now - timedelta(days=rng.randint(0, 3))
        created_at = candidate_day.replace(
            hour=rng.randint(7, 19), minute=rng.randint(0, 59), second=rng.randint(0, 59)
        )

        db.add(
            Transaction(
                transaction_id=tx_id,
                reference_id=reference,
                payer=payer,
                payee=payee,
                payer_fsp=rng.choice(FSP_LIST),
                payee_fsp=rng.choice(FSP_LIST),
                amount=amount,
                currency=currency,
                status=TransactionStatus.SUCCESS.value,
                transfer_state=TransferState.COMMITTED.value,
                created_at=created_at,
                source="seed",
                source_batch_id=batch_id,
            )
        )

        # 92% of transfers settle; the remainder are genuine exceptions.
        if rng.random() < 0.92:
            bank_amount = amount if rng.random() < 0.90 else round(amount * 0.98, 2)
            settle_lag_hours = rng.randint(1, 36)
            if rng.random() < 0.05:
                settle_lag_hours += rng.randint(96, 240)  # late settlement
            db.add(
                BankTransaction(
                    bank_statement_id=f"BS-{9000 + index}",
                    account_number=f"ACC-{rng.randint(10000, 99999)}",
                    counterparty=payer,
                    amount=bank_amount,
                    currency=currency,
                    transaction_type="CREDIT",
                    reference_number=reference,
                    value_date=created_at + timedelta(hours=settle_lag_hours),
                    bank_name="Central Settlement Bank",
                    source="seed",
                    source_batch_id=batch_id,
                )
            )

        if rng.random() < 0.85:
            db.add(
                ERPRecord(
                    erp_id=f"ERP-{5000 + index}",
                    invoice_number=reference,
                    customer_vendor_name=payee,
                    expected_amount=amount,
                    currency=currency,
                    posting_date=created_at - timedelta(days=rng.randint(0, 5)),
                    ledger_account="1100-ACCOUNTS-RECEIVABLE",
                    source="seed",
                    source_batch_id=batch_id,
                )
            )

    # --- operational outflows -------------------------------------------
    # Bank DEBIT lines that are not Mojaloop settlements (supplier payments,
    # payroll, fees). Without these the ledger is credit-only and the cash-flow
    # forecast has a structurally zero outflow side. They carry no reference and
    # distinct counterparty names so the corroboration gate keeps them out of
    # transaction matching.
    outflow_payees = [
        "Payroll Settlement Account", "Office Lease Holdings", "Utility Board Payment",
        "Tax Authority Remittance", "Supplier Clearing House", "Interbank Fee Account",
    ]
    outflow_count = max(1, count // 5)
    for index in range(outflow_count):
        days_ago = rng.randint(0, span_days - 1)
        debit_day = now - timedelta(days=days_ago)
        db.add(
            BankTransaction(
                bank_statement_id=f"BS-OUT-{index + 1:05d}",
                account_number="ACC-99042",
                counterparty=rng.choice(outflow_payees),
                amount=round(rng.uniform(4_000.0, 45_000.0), 2),
                currency="USD",
                transaction_type="DEBIT",
                reference_number=None,
                value_date=debit_day.replace(hour=rng.randint(8, 18)),
                bank_name="Central Settlement Bank",
                source="seed",
                source_batch_id=batch_id,
            )
        )

    db.commit()

    result: Dict[str, object] = {
        "message": f"Demo dataset of {count} transactions successfully seeded.",
        "transactions": count,
        "operational_outflows": outflow_count,
        "history_days": history_days,
        "batch_id": batch_id,
        "seed": seed,
    }

    if reconcile:
        run = run_reconciliation(db, trigger="seed")
        result["reconciliation"] = run
        result["message"] = (
            f"Demo dataset of {count} transactions successfully seeded and reconciled. "
            f"{run['matched_count']}/{run['total_reconciled']} matched."
        )

    return result


__all__ = [
    "apply_manual_overrides",
    "normalize_amounts",
    "process_transfer",
    "reconcile_transaction",
    "run_reconciliation",
    "seed_demo_data",
]
