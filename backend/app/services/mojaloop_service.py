"""Mojaloop hub integration.

The previous version of this module was 32 lines that returned two hardcoded
dictionaries with random UUIDs, performed no network I/O, and persisted nothing.
There was no HTTP client anywhere in the backend, so the "transaction ingestion
layer pulling from Mojaloop settlement and transfer APIs" did not exist.

This module implements that layer:

* a real ``httpx`` client against the transfer and settlement APIs
* FSPIOP-compliant headers (``FSPIOP-Source``, versioned Accept/Content-Type)
* bounded retries with exponential backoff on timeouts and 5xx responses
* tolerant response normalisation, because the hub, the central-ledger admin API
  and the ml-testing-toolkit simulator each wrap payloads differently
* idempotent persistence keyed on ``transactionId``
* an explicit, clearly labelled offline fallback so the demo still runs when no
  hub is reachable

Endpoint paths are configuration rather than constants because they differ
between a real hub deployment and the local toolkit/simulator.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import TransactionStatus, TransferState
from app.models.transaction import Transaction
from app.services.fx_service import get_converter

logger = logging.getLogger(__name__)

FSPIOP_RESOURCE_VERSION = "1.1"


class MojaloopUnavailableError(RuntimeError):
    """Raised when the hub cannot be reached and no fallback is permitted."""


@dataclass
class SyncResult:
    """Outcome of a pull from the hub."""

    source: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: List[str] = field(default_factory=list)
    endpoint: Optional[str] = None
    simulated: bool = False
    duration_seconds: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "endpoint": self.endpoint,
            "simulated": self.simulated,
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "errors": self.errors[:20],
            "duration_seconds": round(self.duration_seconds, 3),
            "message": (
                f"{'Simulated' if self.simulated else 'Fetched'} {self.fetched} transfer(s); "
                f"{self.created} created, {self.updated} updated, {self.skipped} unchanged, "
                f"{self.failed} rejected."
            ),
        }


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


def _fspiop_headers(
    resource: str = "transfers", include_content_type: bool = False
) -> Dict[str, str]:
    """Headers the FSPIOP API requires.

    ``Content-Type`` is only sent when there is a body. FSPIOP validation
    rejects a ``Content-Type`` on a GET, which is what produced a 400 against
    the Testing Toolkit before this was split out.
    """
    version = FSPIOP_RESOURCE_VERSION
    headers = {
        "Accept": f"application/vnd.interoperability.{resource}+json;version={version}",
        "FSPIOP-Source": settings.mojaloop.fsp_id,
        "Date": datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT"),
        "X-Correlation-Id": str(uuid.uuid4()),
    }
    if include_content_type:
        headers["Content-Type"] = (
            f"application/vnd.interoperability.{resource}+json;version={version}"
        )
    return headers


def _json_headers(include_content_type: bool = False) -> Dict[str, str]:
    """Headers for the central-settlement API, which is plain JSON REST."""
    headers = {
        "Accept": "application/json",
        "FSPIOP-Source": settings.mojaloop.fsp_id,
        "X-Correlation-Id": str(uuid.uuid4()),
    }
    if include_content_type:
        headers["Content-Type"] = "application/json"
    return headers


class MojaloopClient:
    """Thin, retrying HTTP client for the Mojaloop hub."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        settlement_base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        config = settings.mojaloop
        self.base_url = (base_url or config.base_url).rstrip("/")
        self.settlement_base_url = (
            settlement_base_url or config.effective_settlement_base_url
        ).rstrip("/")
        self.timeout = timeout if timeout is not None else config.timeout_seconds
        self.max_retries = max_retries if max_retries is not None else config.max_retries
        self.backoff = config.retry_backoff_seconds
        self.verify_tls = config.verify_tls

    # -- low level -------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        resource: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        fspiop: bool = True,
    ) -> Any:
        """Issue a request, retrying transient failures with backoff.

        ``fspiop=False`` sends plain JSON headers. The central-settlement API is
        an ordinary JSON REST API, not an FSPIOP interoperability API: sending it
        ``Accept: application/vnd.interoperability.settlements+json;version=1.1``
        makes content negotiation fail and the request 404s.
        """
        import httpx

        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                headers = (
                    _fspiop_headers(resource, include_content_type=json_body is not None)
                    if fspiop
                    else _json_headers(include_content_type=json_body is not None)
                )
                with httpx.Client(timeout=self.timeout, verify=self.verify_tls) as client:
                    response = client.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json_body,
                    )

                # 5xx and 429 are worth retrying; 4xx is not.
                if response.status_code >= 500 or response.status_code == 429:
                    last_error = RuntimeError(
                        f"{response.status_code} from {url}: {response.text[:200]}"
                    )
                    raise last_error

                response.raise_for_status()

                if not response.content:
                    return None
                try:
                    return response.json()
                except ValueError:
                    return response.text

            except httpx.HTTPStatusError as exc:
                # A definitive client error: do not retry.
                logger.warning("Mojaloop request failed permanently: %s", exc)
                raise
            except Exception as exc:  # timeouts, connection errors, 5xx
                last_error = exc
                if attempt >= self.max_retries:
                    break
                delay = self.backoff * (2 ** (attempt - 1))
                logger.warning(
                    "Mojaloop request to %s failed (attempt %d/%d): %s; retrying in %.2fs",
                    url,
                    attempt,
                    self.max_retries,
                    exc,
                    delay,
                )
                time.sleep(delay)

        raise MojaloopUnavailableError(
            f"Could not reach {url} after {self.max_retries} attempt(s): {last_error}"
        )

    # -- transfers -------------------------------------------------------
    #
    # There is deliberately no "list transfers" method. FSPIOP exposes only
    # GET /transfers/{id}; there is no bulk/collection endpoint on the transfer
    # API. Transfers reach this system by *push* (the hub calls our
    # POST /webhooks/transfers), and this client is used to verify or enrich an
    # individual transfer we already know about.

    def fetch_transfer(self, transfer_id: str) -> Optional[Dict[str, Any]]:
        """Fetch one transfer by id (FSPIOP ``GET /transfers/{ID}``).

        Returns ``None`` when the hub reports it does not exist.

        Note on FSPIOP semantics: on a real hub this operation is
        *asynchronous* -- it answers ``202 Accepted`` and the hub later calls
        back ``PUT /transfers/{ID}`` to the requester's endpoint. A synchronous
        body is returned by the Testing Toolkit mock and by admin-style APIs.
        ``ASYNC_ACCEPTED`` is returned for the 202 case so the caller can report
        "verification requested" rather than mistaking it for a failure.
        """
        import httpx

        url = f"{self.base_url}{settings.mojaloop.transfers_path}/{transfer_id}"
        try:
            with httpx.Client(timeout=self.timeout, verify=self.verify_tls) as client:
                response = client.get(url, headers=_fspiop_headers("transfers"))
        except Exception as exc:
            raise MojaloopUnavailableError(f"Could not reach {url}: {exc}") from exc

        if response.status_code == 404:
            return None

        if response.status_code == 202:
            return {"__async_accepted__": True, "transferId": transfer_id}

        response.raise_for_status()

        if not response.content:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None

        transfers = _extract_transfer_list(payload)
        return transfers[0] if transfers else None

    # -- settlement ------------------------------------------------------

    def _settlement_url(self, path: str) -> str:
        """Build a settlement URL, honouring the configured API version prefix.

        ``settlements_2.0`` in the Testing Toolkit is mounted under ``/v2``.
        """
        prefix = settings.mojaloop.settlement_api_prefix.strip("/")
        base = self.settlement_base_url
        if prefix:
            return f"{base}/{prefix}{path}"
        return f"{base}{path}"

    def fetch_settlement_windows(
        self,
        state: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """List settlement windows, which is what reconciliation is scoped to."""
        url = self._settlement_url(settings.mojaloop.settlement_windows_path)
        params: Dict[str, Any] = {}
        if state:
            params["state"] = state
        if from_date:
            params["fromDateTime"] = _iso(from_date)
        if to_date:
            params["toDateTime"] = _iso(to_date)

        payload = self._request(
            "GET", url, "settlementWindows", params=params or None, fspiop=False
        )
        return _extract_collection(payload, "settlementWindows", "windows")

    # There is deliberately no `fetch_settlement_window(id)` and no
    # `fetch_settlements()` list method. The Mojaloop central-settlement API
    # (both 1.0 and 2.0) exposes:
    #
    #   GET  /settlementWindows       list windows      <- supported
    #   POST /settlementWindows/{id}  close a window
    #   POST /settlements             create a settlement
    #   GET  /settlements/{id}        one settlement    <- supported
    #
    # A single window is therefore read from the list, and settlements are
    # fetched by id. Adding methods for GET endpoints that do not exist would
    # repeat the mistake that the invalid "list transfers" call represented.

    def fetch_settlement(self, settlement_id: str) -> Optional[Dict[str, Any]]:
        """Fetch one settlement by id (``GET /settlements/{id}``)."""
        url = self._settlement_url(
            f"{settings.mojaloop.settlements_path}/{settlement_id}"
        )
        payload = self._request("GET", url, "settlements", fspiop=False)
        if isinstance(payload, dict):
            return payload
        found = _extract_collection(payload, "settlements", "data")
        return found[0] if found else None

    # -- health ----------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        """Probe reachability without raising, for the status endpoint.

        Probes the settlement-window list rather than ``/health``: the Testing
        Toolkit has no ``/health`` route, so probing it reported a reachable hub
        as unreachable.
        """
        started = time.perf_counter()
        url = self._settlement_url(settings.mojaloop.settlement_windows_path)
        try:
            payload = self._request(
                "GET", url, "settlementWindows", fspiop=False
            )
            windows = _extract_collection(payload, "settlementWindows", "windows")
            return {
                "reachable": True,
                "base_url": self.base_url,
                "probe": url,
                "settlement_windows_visible": len(windows),
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        except Exception as exc:
            return {
                "reachable": False,
                "base_url": self.base_url,
                "probe": url,
                "error": str(exc)[:300],
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            }


# ---------------------------------------------------------------------------
# Payload normalisation
# ---------------------------------------------------------------------------


def _iso(moment: datetime) -> str:
    """Format a timestamp the way the settlement API expects."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _extract_collection(payload: Any, *keys: str) -> List[Dict[str, Any]]:
    """Pull a list of objects out of a response of uncertain shape.

    The hub, the central-settlement service and the Testing Toolkit each wrap
    collections differently (bare array, ``{settlementWindows: [...]}``,
    ``{data: [...]}``), so the shape is probed rather than assumed.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in (*keys, "data", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        # A single object rather than a collection.
        if payload:
            return [payload]
    return []


def _extract_transfer_list(payload: Any) -> List[Dict[str, Any]]:
    """Find the transfer collection inside a variety of response shapes.

    A real hub, the central-ledger admin API and the testing toolkit each wrap
    the payload differently, so the shape is probed rather than assumed.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("transfers", "transactions", "data", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        # A single transfer object.
        if any(k in payload for k in ("transferId", "transactionId", "transaction_id")):
            return [payload]
    return []


def _first(mapping: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return default


def _party_name(value: Any) -> Optional[str]:
    """Pull a human name out of an FSPIOP party structure or a plain string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        party = value.get("partyIdInfo") or value
        name = _first(
            value,
            "name",
            "displayName",
            "partyName",
            default=None,
        )
        if isinstance(name, str) and name.strip():
            return name.strip()

        personal = value.get("personalInfo") or {}
        complex_name = personal.get("complexName") if isinstance(personal, dict) else None
        if isinstance(complex_name, dict):
            parts = [
                complex_name.get("firstName"),
                complex_name.get("middleName"),
                complex_name.get("lastName"),
            ]
            joined = " ".join(p for p in parts if p)
            if joined.strip():
                return joined.strip()

        if isinstance(party, dict):
            identifier = _first(party, "partyIdentifier", "partyId", "idValue")
            if identifier:
                return str(identifier)
    return None


def _fsp_id(value: Any, fallback: str = "UNKNOWN-FSP") -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        party = value.get("partyIdInfo") or value
        if isinstance(party, dict):
            fsp = _first(party, "fspId", "fsp", "fspid")
            if fsp:
                return str(fsp)
    return fallback


def normalize_transfer(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map a hub transfer payload onto our unified transaction shape.

    Raises ``ValueError`` when a mandatory field is missing, so the caller can
    report the specific record instead of failing the whole sync.
    """
    transaction_id = _first(
        raw, "transactionId", "transferId", "transaction_id", "transfer_id", "id"
    )
    if not transaction_id:
        raise ValueError("payload carries no transactionId/transferId")

    # Amount may be a scalar or an FSPIOP {amount, currency} object.
    amount_block = _first(raw, "amount", "transferAmount", default=None)
    currency = _first(raw, "currency", default=None)

    if isinstance(amount_block, dict):
        amount_value = _first(amount_block, "amount", "value")
        currency = _first(amount_block, "currency", default=currency)
    else:
        amount_value = amount_block

    if amount_value is None:
        raise ValueError(f"transfer {transaction_id} carries no amount")

    try:
        amount = float(str(amount_value).replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"transfer {transaction_id} has a non-numeric amount {amount_value!r}"
        ) from exc

    payer_raw = _first(raw, "payer", "payerParty", "from", "debtor")
    payee_raw = _first(raw, "payee", "payeeParty", "to", "creditor")

    state = str(
        _first(raw, "transferState", "transfer_state", "state", default=TransferState.COMMITTED.value)
    ).upper()

    completed = _first(raw, "completedTimestamp", "completed_at", "transferDate", "createdAt")
    created_at = _parse_timestamp(completed) or datetime.utcnow()

    return {
        "transaction_id": str(transaction_id),
        "reference_id": _opt_str(
            _first(raw, "referenceId", "reference_id", "reference", "homeTransactionId")
        ),
        "payer": _party_name(payer_raw) or "Unknown Payer",
        "payee": _party_name(payee_raw) or "Unknown Payee",
        "payer_fsp": _fsp_id(_first(raw, "payerFsp", "payer_fsp", default=payer_raw)),
        "payee_fsp": _fsp_id(_first(raw, "payeeFsp", "payee_fsp", default=payee_raw)),
        "amount": amount,
        "currency": str(currency or settings.fx.base_currency).upper(),
        "transfer_state": state,
        "status": (
            TransactionStatus.SUCCESS.value
            if state == TransferState.COMMITTED.value
            else TransactionStatus.PENDING.value
        ),
        "created_at": created_at,
        "raw_payload": json.dumps(raw, default=str)[:20000],
    }


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    normalised = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalised)
        return parsed.replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def persist_transfers(
    db: Session, transfers: Sequence[Dict[str, Any]], source: str = "mojaloop"
) -> SyncResult:
    """Upsert normalised transfers. Idempotent on ``transaction_id``."""
    result = SyncResult(source=source)
    result.fetched = len(transfers)
    if not transfers:
        return result

    normalised: List[Dict[str, Any]] = []
    for raw in transfers:
        try:
            normalised.append(normalize_transfer(raw))
        except ValueError as exc:
            result.failed += 1
            result.errors.append(str(exc))

    if not normalised:
        return result

    ids = [item["transaction_id"] for item in normalised]
    existing: Dict[str, Transaction] = {
        row.transaction_id: row
        for row in db.query(Transaction).filter(Transaction.transaction_id.in_(ids)).all()
    }

    converter = get_converter(db)
    base = converter.base_currency
    now = datetime.utcnow()
    batch_id = f"mojaloop-{uuid.uuid4().hex[:10]}"

    for item in normalised:
        conversion = converter.convert(item["amount"], item["currency"], base)
        row = existing.get(item["transaction_id"])

        if row is None:
            db.add(
                Transaction(
                    transaction_id=item["transaction_id"],
                    reference_id=item["reference_id"],
                    payer=item["payer"],
                    payee=item["payee"],
                    payer_fsp=item["payer_fsp"],
                    payee_fsp=item["payee_fsp"],
                    amount=item["amount"],
                    currency=item["currency"],
                    status=item["status"],
                    transfer_state=item["transfer_state"],
                    created_at=item["created_at"],
                    amount_base=conversion.amount,
                    base_currency=base,
                    fx_rate_used=conversion.rate,
                    normalized_at=now,
                    source=source,
                    source_batch_id=batch_id,
                    idempotency_key=item["transaction_id"],
                    raw_payload=item["raw_payload"],
                    ingested_at=now,
                )
            )
            result.created += 1
            continue

        # Known transfer: advance its state only if something actually changed.
        changed = False
        if row.transfer_state != item["transfer_state"]:
            row.transfer_state = item["transfer_state"]
            changed = True
        if row.status != item["status"]:
            row.status = item["status"]
            changed = True
        if row.amount_base is None:
            row.amount_base = conversion.amount
            row.base_currency = base
            row.fx_rate_used = conversion.rate
            row.normalized_at = now
            changed = True

        if changed:
            row.raw_payload = item["raw_payload"]
            result.updated += 1
        else:
            result.skipped += 1

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to persist Mojaloop transfers")
        raise MojaloopUnavailableError(f"Persisting transfers failed: {exc}") from exc

    return result


# ---------------------------------------------------------------------------
# High-level sync
# ---------------------------------------------------------------------------


def verify_transfer(db: Session, transaction_id: str) -> Dict[str, Any]:
    """Verify one stored transfer against the hub's own record of it.

    This is the replacement for the old bulk ``fetch_transfers`` pull. Because
    FSPIOP has no collection endpoint, verification is necessarily per-transfer:
    we already hold the record (it arrived by webhook push) and we ask the hub
    to confirm its amount, currency and state.

    Any disagreement is reported rather than silently corrected -- a mismatch
    between our copy and the hub's is itself a reconciliation finding.
    """
    local = (
        db.query(Transaction).filter(Transaction.transaction_id == transaction_id).first()
    )
    if local is None:
        return {
            "transaction_id": transaction_id,
            "verified": False,
            "error": "not held locally; nothing to verify",
        }

    if not settings.mojaloop.enabled:
        return {
            "transaction_id": transaction_id,
            "verified": False,
            "error": "MOJALOOP_ENABLED is false; cannot reach the hub",
            "local": _local_view(local),
        }

    try:
        remote_raw = MojaloopClient().fetch_transfer(transaction_id)
    except Exception as exc:
        logger.warning("Transfer verification failed for %s: %s", transaction_id, exc)
        return {
            "transaction_id": transaction_id,
            "verified": False,
            "error": str(exc)[:300],
            "local": _local_view(local),
        }

    if remote_raw is None:
        return {
            "transaction_id": transaction_id,
            "verified": False,
            "exists_on_hub": False,
            "discrepancies": ["the hub has no record of this transfer"],
            "local": _local_view(local),
        }

    if remote_raw.get("__async_accepted__"):
        # A real FSPIOP hub answers GET /transfers/{ID} with 202 and calls back
        # PUT /transfers/{ID} later. Report that honestly instead of pretending
        # the comparison happened.
        return {
            "transaction_id": transaction_id,
            "verified": False,
            "pending_callback": True,
            "local": _local_view(local),
            "message": (
                "The hub accepted the lookup asynchronously (202). It will call back "
                "PUT /transfers/{ID}; re-check after the callback arrives."
            ),
        }

    try:
        remote = normalize_transfer(remote_raw)
    except ValueError as exc:
        return {
            "transaction_id": transaction_id,
            "verified": False,
            "error": f"hub payload could not be parsed: {exc}",
            "local": _local_view(local),
        }

    discrepancies: List[str] = []
    if abs(float(local.amount or 0.0) - float(remote["amount"])) > 1e-6:
        discrepancies.append(
            f"amount differs: local {local.amount} vs hub {remote['amount']}"
        )
    if (local.currency or "").upper() != remote["currency"]:
        discrepancies.append(
            f"currency differs: local {local.currency} vs hub {remote['currency']}"
        )
    if (local.transfer_state or "").upper() != remote["transfer_state"]:
        discrepancies.append(
            f"state differs: local {local.transfer_state} vs hub {remote['transfer_state']}"
        )

    return {
        "transaction_id": transaction_id,
        "verified": not discrepancies,
        "exists_on_hub": True,
        "discrepancies": discrepancies,
        "local": _local_view(local),
        "hub": {
            "amount": remote["amount"],
            "currency": remote["currency"],
            "transfer_state": remote["transfer_state"],
            "payer": remote["payer"],
            "payee": remote["payee"],
        },
    }


def _local_view(row: Transaction) -> Dict[str, Any]:
    return {
        "amount": row.amount,
        "currency": row.currency,
        "transfer_state": row.transfer_state,
        "status": row.status,
        "payer": row.payer,
        "payee": row.payee,
        "source": row.source,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def connection_status() -> Dict[str, Any]:
    """Report integration configuration and reachability."""
    config = settings.mojaloop
    status: Dict[str, Any] = {
        "enabled": config.enabled,
        "base_url": config.base_url,
        "settlement_base_url": config.effective_settlement_base_url,
        "fsp_id": config.fsp_id,
        "fspiop_version": FSPIOP_RESOURCE_VERSION,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
        "fallback_to_simulator": config.fallback_to_simulator,
    }
    if config.enabled:
        status["hub"] = MojaloopClient().health()
    else:
        status["hub"] = {
            "reachable": False,
            "reason": "integration disabled (MOJALOOP_ENABLED=false)",
        }
    return status


# ---------------------------------------------------------------------------
# Offline fallback
# ---------------------------------------------------------------------------


def simulated_transfers() -> Dict[str, Any]:
    """Deterministic offline sample in FSPIOP-like shape.

    Retained so the system is demonstrable without a running hub, but it is
    always reported as ``simulated: true`` by the sync path. Ids are derived
    from the content rather than random, so repeated calls are idempotent
    instead of inserting new rows every time (the original version minted a
    fresh ``uuid4`` per call).
    """
    samples = [
        ("Alice Traders", "Bob Supplies Ltd", "BankA", "BankB", 5000.0, "INR", "REF-SIM-001"),
        ("John Imports", "David Logistics", "BankX", "BankY", 12000.0, "INR", "REF-SIM-002"),
        ("Nairobi Foods", "Safari Ventures", "DFSP-Alpha", "PayCentral", 48000.0, "KES", "REF-SIM-003"),
    ]

    transactions: List[Dict[str, Any]] = []
    for payer, payee, payer_fsp, payee_fsp, amount, currency, reference in samples:
        deterministic_id = "TX-SIM-" + uuid.uuid5(
            uuid.NAMESPACE_URL, f"{payer}|{payee}|{amount}|{currency}|{reference}"
        ).hex[:12].upper()
        transactions.append(
            {
                "transactionId": deterministic_id,
                "referenceId": reference,
                "payer": payer,
                "payee": payee,
                "payerFsp": payer_fsp,
                "payeeFsp": payee_fsp,
                "amount": {"amount": amount, "currency": currency},
                "transferState": TransferState.COMMITTED.value,
                "completedTimestamp": datetime.utcnow().isoformat() + "Z",
            }
        )

    return {
        "source": "builtin-simulator",
        "simulated": True,
        "count": len(transactions),
        "transactions": transactions,
    }


__all__ = [
    "MojaloopClient",
    "MojaloopUnavailableError",
    "SyncResult",
    "connection_status",
    "normalize_transfer",
    "persist_transfers",
    "simulated_transfers",
    "verify_transfer",
]
