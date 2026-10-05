"""Settlement window and settlement synchronisation.

This is the "pull" half of the Mojaloop integration. Transfers arrive by push
(the hub calls our webhook); settlement windows are read from the central
settlement API and used to give reconciliation a meaningful period boundary.

Why windows matter here: a settlement window is the interval the hub itself
considers closed and final. Reconciling "the window that closed at 02:00" is a
defensible unit of work with a fixed boundary, whereas reconciling "the last 7
days" re-does settled work and can straddle an open window whose transfers are
still arriving.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import RunScope, SettlementWindowState
from app.models.settlement import Settlement, SettlementWindow
from app.models.transaction import Transaction
from app.services.mojaloop_service import MojaloopClient, _parse_timestamp

logger = logging.getLogger(__name__)

# States that mean the hub has stopped accepting transfers into the window, so
# its boundary is stable enough to reconcile against.
RECONCILABLE_STATES = {
    SettlementWindowState.CLOSED.value,
    SettlementWindowState.PENDING_SETTLEMENT.value,
    SettlementWindowState.SETTLED.value,
}


def _first(mapping: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return default


def normalize_window(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map a hub settlement-window payload onto our columns."""
    window_id = _first(raw, "settlementWindowId", "id", "windowId")
    if window_id in (None, ""):
        raise ValueError("settlement window payload carries no id")

    state = str(_first(raw, "state", "settlementWindowState", default="OPEN")).upper()

    return {
        "window_id": str(window_id),
        "state": state,
        "reason": _first(raw, "reason", "settlementWindowReason"),
        "created_date": _parse_timestamp(
            _first(raw, "createdDate", "settlementWindowOpenedDate", "createdAt")
        ),
        "changed_date": _parse_timestamp(
            _first(raw, "changedDate", "settlementWindowCloseDate", "updatedAt")
        ),
        "raw_payload": json.dumps(raw, default=str)[:20000],
    }


def normalize_settlement(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map a hub settlement payload onto our columns."""
    settlement_id = _first(raw, "settlementId", "id")
    if settlement_id in (None, ""):
        raise ValueError("settlement payload carries no id")

    windows = raw.get("settlementWindows")
    window_ids: List[str] = []
    if isinstance(windows, list):
        for item in windows:
            if isinstance(item, dict):
                value = _first(item, "id", "settlementWindowId")
                if value is not None:
                    window_ids.append(str(value))
            elif item is not None:
                window_ids.append(str(item))

    participants = raw.get("participants")
    participant_count = len(participants) if isinstance(participants, list) else 0
    net_amount, currency = _net_position(participants)

    return {
        "settlement_id": str(settlement_id),
        "state": str(_first(raw, "state", default="")).upper() or None,
        "settlement_model": _first(raw, "settlementModel", "model"),
        "reason": _first(raw, "reason"),
        "created_date": _parse_timestamp(_first(raw, "createdDate", "createdAt")),
        "changed_date": _parse_timestamp(_first(raw, "changedDate", "updatedAt")),
        "window_ids": ",".join(window_ids) or None,
        "participant_count": participant_count,
        "net_amount": net_amount,
        "currency": currency,
        "raw_payload": json.dumps(raw, default=str)[:20000],
    }


def _net_position(participants: Any) -> Tuple[Optional[float], Optional[str]]:
    """Total settled value and currency from a settlement's participant accounts.

    The hub reports a ``netSettlementAmount`` per participant account, and those
    net out to roughly zero across the whole settlement (one party's debit is
    another's credit). The useful figure for a treasury view is the gross value
    moved, so the positive side is summed.

    Returns ``(None, None)`` when the payload carries no amounts, and reports
    only a single currency: a mixed-currency settlement cannot be summarised by
    one number, so it is left unset rather than silently adding currencies
    together.
    """
    if not isinstance(participants, list):
        return None, None

    total = 0.0
    currencies: set[str] = set()
    seen_any = False

    for participant in participants:
        if not isinstance(participant, dict):
            continue
        for account in participant.get("accounts") or []:
            if not isinstance(account, dict):
                continue
            net = account.get("netSettlementAmount")
            if not isinstance(net, dict):
                continue
            raw_amount = net.get("amount")
            if raw_amount is None:
                continue
            try:
                value = float(str(raw_amount).replace(",", ""))
            except (TypeError, ValueError):
                continue
            seen_any = True
            code = net.get("currency")
            if code:
                currencies.add(str(code).strip().upper())
            if value > 0:
                total += value

    if not seen_any:
        return None, None
    if len(currencies) > 1:
        logger.info(
            "Settlement spans %d currencies (%s); net amount left unset",
            len(currencies),
            ", ".join(sorted(currencies)),
        )
        return None, None

    return round(total, 2), (next(iter(currencies)) if currencies else None)


# ---------------------------------------------------------------------------
# Window scope
# ---------------------------------------------------------------------------


def window_scope(window: SettlementWindow) -> Tuple[datetime, datetime]:
    """The time interval a window covers, as (from, to).

    ``created_date`` is when the window opened. ``changed_date`` is when it last
    transitioned, which for a closed window is when it closed. An open window
    has no end yet, so it runs to now.

    A window whose open and close timestamps are equal (or whose open timestamp
    is missing) describes a zero-width interval, which would scope a
    reconciliation run to nothing. That happens with mocked hub data and with
    hubs that only report the transition time. In that case the window is
    treated as the nominal window length ending at its close time, controlled by
    ``MOJALOOP_SETTLEMENT_WINDOW_HOURS``.
    """
    nominal = timedelta(hours=settings.mojaloop.settlement_window_hours)
    now = datetime.utcnow()

    if window.state == SettlementWindowState.OPEN.value or window.changed_date is None:
        end = now
    else:
        end = window.changed_date

    start = window.created_date

    # Degenerate or missing interval: fall back to the nominal window length.
    if start is None or (end - start) < timedelta(seconds=1):
        start = end - nominal

    if end < start:
        end = start

    return start, end


def count_transactions_in_window(db: Session, window: SettlementWindow) -> int:
    start, end = window_scope(window)
    return int(
        db.query(func.count(Transaction.id))
        .filter(Transaction.created_at >= start, Transaction.created_at <= end)
        .scalar()
        or 0
    )


def transaction_ids_in_window(db: Session, window: SettlementWindow) -> List[str]:
    """Business ids of the transactions inside a window's interval.

    Lets reconciliation results be filtered by window without adding a column:
    the link between a result and a window is the transaction's timestamp, which
    is exactly how a scoped run selects its work in the first place.
    """
    start, end = window_scope(window)
    return [
        row[0]
        for row in db.query(Transaction.transaction_id)
        .filter(Transaction.created_at >= start, Transaction.created_at <= end)
        .all()
    ]


# ---------------------------------------------------------------------------
# Synchronisation
# ---------------------------------------------------------------------------


def sync_windows(
    db: Session,
    state: Optional[str] = None,
    since_hours: int = 168,
) -> Dict[str, Any]:
    """Pull settlement windows from the hub and upsert them.

    Idempotent on ``window_id``: a window already held is updated in place when
    its state has moved on, rather than duplicated.
    """
    if not settings.mojaloop.enabled:
        return {
            "enabled": False,
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "message": (
                "Mojaloop integration is disabled. Set MOJALOOP_ENABLED=true (and "
                "MOJALOOP_SETTLEMENT_BASE_URL if the settlement API is on another host) "
                "to read settlement windows."
            ),
        }

    client = MojaloopClient()
    try:
        raw_windows = client.fetch_settlement_windows(
            state=state,
            from_date=datetime.utcnow() - timedelta(hours=since_hours),
            to_date=datetime.utcnow(),
        )
    except Exception as exc:
        logger.warning("Settlement window fetch failed: %s", exc)
        return {
            "enabled": True,
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "error": str(exc)[:300],
            "message": "Could not read settlement windows from the hub.",
        }

    created = updated = unchanged = failed = 0
    errors: List[str] = []
    now = datetime.utcnow()

    for raw in raw_windows:
        try:
            mapped = normalize_window(raw)
        except ValueError as exc:
            failed += 1
            errors.append(str(exc))
            continue

        row = (
            db.query(SettlementWindow)
            .filter(SettlementWindow.window_id == mapped["window_id"])
            .first()
        )

        if row is None:
            row = SettlementWindow(**mapped, synced_at=now)
            db.add(row)
            created += 1
        else:
            changed = False
            for field in ("state", "reason", "created_date", "changed_date"):
                if getattr(row, field) != mapped[field]:
                    setattr(row, field, mapped[field])
                    changed = True
            row.raw_payload = mapped["raw_payload"]
            row.synced_at = now
            if changed:
                updated += 1
            else:
                unchanged += 1

    db.commit()

    # Record how many transactions fall inside each window, which is what makes
    # the window useful as a reconciliation scope.
    for row in db.query(SettlementWindow).all():
        row.transactions_in_window = count_transactions_in_window(db, row)
    db.commit()

    logger.info(
        "Settlement window sync: %d fetched, %d created, %d updated, %d unchanged",
        len(raw_windows),
        created,
        updated,
        unchanged,
    )

    return {
        "enabled": True,
        "fetched": len(raw_windows),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "failed": failed,
        "errors": errors[:10],
        "message": (
            f"Synced {len(raw_windows)} settlement window(s): {created} new, "
            f"{updated} updated."
        ),
    }


def sync_settlements(db: Session, settlement_ids: Sequence[str]) -> Dict[str, Any]:
    """Fetch specific settlements by id and upsert them.

    The settlement API has no list endpoint (``GET /settlements`` does not
    exist; ``POST /settlements`` creates one). Settlements are therefore read
    individually by id, which the caller supplies.
    """
    if not settings.mojaloop.enabled:
        return {
            "enabled": False,
            "fetched": 0,
            "message": "Mojaloop integration is disabled.",
        }

    if not settlement_ids:
        return {
            "enabled": True,
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "message": (
                "No settlement ids supplied. GET /settlements is not part of the "
                "Mojaloop settlement API, so settlements must be requested by id."
            ),
        }

    client = MojaloopClient()
    created = updated = failed = 0
    errors: List[str] = []
    fetched = 0
    now = datetime.utcnow()

    for settlement_id in settlement_ids:
        try:
            raw = client.fetch_settlement(str(settlement_id))
        except Exception as exc:
            failed += 1
            errors.append(f"{settlement_id}: {str(exc)[:200]}")
            continue

        if raw is None:
            failed += 1
            errors.append(f"{settlement_id}: not found on the hub")
            continue

        fetched += 1
        try:
            mapped = normalize_settlement(raw)
        except ValueError as exc:
            failed += 1
            errors.append(f"{settlement_id}: {exc}")
            continue

        row = (
            db.query(Settlement)
            .filter(Settlement.settlement_id == mapped["settlement_id"])
            .first()
        )
        if row is None:
            db.add(Settlement(**mapped, synced_at=now))
            created += 1
        else:
            for field, value in mapped.items():
                setattr(row, field, value)
            row.synced_at = now
            updated += 1

    db.commit()

    return {
        "enabled": True,
        "requested": len(settlement_ids),
        "fetched": fetched,
        "created": created,
        "updated": updated,
        "failed": failed,
        "errors": errors[:10],
        "message": f"Synced {fetched} settlement(s): {created} new, {updated} updated.",
    }


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_windows(
    db: Session,
    state: Optional[str] = None,
    reconciled: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """Stored settlement windows, newest first."""
    query = db.query(SettlementWindow)
    if state:
        query = query.filter(SettlementWindow.state == state.upper())
    if reconciled is True:
        query = query.filter(SettlementWindow.reconciled_at.isnot(None))
    elif reconciled is False:
        query = query.filter(SettlementWindow.reconciled_at.is_(None))

    total = query.count()
    rows = (
        query.order_by(
            SettlementWindow.created_date.desc().nullslast(),
            SettlementWindow.id.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(rows),
        "items": [_window_view(db, row) for row in rows],
    }


def _window_view(db: Session, row: SettlementWindow) -> Dict[str, Any]:
    start, end = window_scope(row)
    return {
        "window_id": row.window_id,
        "state": row.state,
        "reason": row.reason,
        "created_date": row.created_date.isoformat() if row.created_date else None,
        "changed_date": row.changed_date.isoformat() if row.changed_date else None,
        "scope_from": start.isoformat(),
        "scope_to": end.isoformat(),
        "transactions_in_window": row.transactions_in_window,
        "is_reconcilable": row.state in RECONCILABLE_STATES,
        "reconciled_at": row.reconciled_at.isoformat() if row.reconciled_at else None,
        "last_run_id": row.last_run_id,
        "synced_at": row.synced_at.isoformat() if row.synced_at else None,
    }


def pending_windows(db: Session) -> List[SettlementWindow]:
    """Closed windows that have not been reconciled yet.

    This is what the scheduler consumes: a window transitions to CLOSED, and the
    next cycle reconciles exactly that window instead of the whole database.
    """
    return (
        db.query(SettlementWindow)
        .filter(
            SettlementWindow.state.in_(tuple(RECONCILABLE_STATES)),
            SettlementWindow.reconciled_at.is_(None),
        )
        .order_by(SettlementWindow.created_date.asc().nullsfirst())
        .all()
    )


def get_window(db: Session, window_id: str) -> Optional[SettlementWindow]:
    return (
        db.query(SettlementWindow)
        .filter(SettlementWindow.window_id == str(window_id))
        .first()
    )


# ---------------------------------------------------------------------------
# Window-scoped reconciliation
# ---------------------------------------------------------------------------


def reconcile_window(
    db: Session,
    window_id: str,
    trigger: str = "settlement-window",
    refresh_forecast: bool = False,
) -> Dict[str, Any]:
    """Reconcile only the transactions inside one settlement window."""
    from app.services.reconciliation_service import run_reconciliation

    window = get_window(db, window_id)
    if window is None:
        raise ValueError(f"settlement window '{window_id}' is not held locally")

    if window.state == SettlementWindowState.OPEN.value:
        logger.warning(
            "Window %s is still OPEN; transfers may still arrive into it",
            window.window_id,
        )

    start, end = window_scope(window)
    result = run_reconciliation(
        db,
        trigger=trigger,
        refresh_forecast=refresh_forecast,
        scope=RunScope.SETTLEMENT_WINDOW.value,
        settlement_window_id=window.window_id,
        scope_from=start,
        scope_to=end,
    )

    window.reconciled_at = datetime.utcnow()
    window.last_run_id = result.get("run_id")
    window.transactions_in_window = result.get("total_reconciled", 0)
    db.commit()

    result["settlement_window"] = {
        "window_id": window.window_id,
        "state": window.state,
        "scope_from": start.isoformat(),
        "scope_to": end.isoformat(),
    }
    return result


def reconcile_pending_windows(
    db: Session, limit: int = 10, refresh_forecast: bool = True
) -> Dict[str, Any]:
    """Reconcile every closed window that has not been reconciled yet."""
    windows = pending_windows(db)[:limit]
    if not windows:
        return {
            "windows_processed": 0,
            "results": [],
            "message": "No closed settlement windows are awaiting reconciliation.",
        }

    results: List[Dict[str, Any]] = []
    for index, window in enumerate(windows):
        # Refresh the forecast once, after the last window, rather than per run.
        is_last = index == len(windows) - 1
        try:
            outcome = reconcile_window(
                db,
                window.window_id,
                trigger="scheduled-settlement-window",
                refresh_forecast=refresh_forecast and is_last,
            )
            results.append(
                {
                    "window_id": window.window_id,
                    "run_id": outcome.get("run_id"),
                    "total": outcome.get("total_reconciled"),
                    "matched": outcome.get("matched_count"),
                    "anomalies": outcome.get("anomalies_detected"),
                }
            )
        except Exception as exc:
            logger.exception("Reconciling window %s failed", window.window_id)
            results.append({"window_id": window.window_id, "error": str(exc)[:300]})

    return {
        "windows_processed": len(results),
        "results": results,
        "message": f"Reconciled {len(results)} settlement window(s).",
    }


__all__ = [
    "RECONCILABLE_STATES",
    "count_transactions_in_window",
    "transaction_ids_in_window",
    "get_window",
    "list_windows",
    "normalize_settlement",
    "normalize_window",
    "pending_windows",
    "reconcile_pending_windows",
    "reconcile_window",
    "sync_settlements",
    "sync_windows",
    "window_scope",
]
