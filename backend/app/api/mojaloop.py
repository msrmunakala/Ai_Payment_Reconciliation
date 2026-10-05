"""Mojaloop webhook receiver and hub sync endpoints.

Changes from the original router:

* ``POST /transfers`` verifies an HMAC signature when ``WEBHOOK_SECRET`` is set.
  Previously anyone who could reach the endpoint could inject transactions.
* The handler no longer triggers a full re-reconciliation of the entire database
  on every inbound message; it reconciles just that transfer, in a worker thread
  so the event loop is not blocked by synchronous database and CPU work.
* ``/sync`` and ``/settlement-windows`` expose the real transfer and settlement
  APIs. ``/simulate-fetch`` is retained but now clearly labelled as simulated.
"""


from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.db import get_db
from app.schemas.mojaloop import (
    MojaloopQuoteRequest,
    MojaloopTransactionRequest,
    MojaloopTransfer,
)
from app.services.mojaloop_service import (
    connection_status,
    persist_transfers,
    simulated_transfers,
    verify_transfer,
)
from app.services.reconciliation_service import process_transfer
from app.utils.helpers import verify_hmac_signature

router = APIRouter(prefix="/webhooks", tags=["Mojaloop Webhooks"])


async def verify_webhook_signature(request: Request) -> None:
    """Reject unsigned or mis-signed payloads when a secret is configured.

    Registered as a *route-level dependency* rather than called inside the
    handler. FastAPI solves dependencies before raising body-validation errors,
    so an unauthenticated caller gets 401 and never learns whether their payload
    would have been schema-valid. Calling this from inside the handler meant
    Pydantic rejected a malformed body with 422 before the signature was ever
    examined.

    No secret configured means verification is skipped, which is required for the
    ml-testing-toolkit simulator (it does not sign). That state is surfaced as a
    startup warning rather than being silent.
    """
    secret = settings.security.webhook_secret
    if not secret:
        return

    header_name = settings.security.webhook_signature_header
    provided = request.headers.get(header_name)
    if not provided:
        raise HTTPException(
            status_code=401,
            detail=f"Missing {header_name}; webhook signature verification is enabled.",
        )

    body = await request.body()
    if not verify_hmac_signature(body, provided, secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")


@router.post("/transfers", dependencies=[Depends(verify_webhook_signature)])
async def receive_transfer(
    transfer: MojaloopTransfer,
    db: Session = Depends(get_db),
):
    """Receive a transfer notification and reconcile that transfer only."""
    result = await run_in_threadpool(process_transfer, db, transfer)
    return {"success": True, "data": result}


@router.post("/quotes", dependencies=[Depends(verify_webhook_signature)])
async def receive_quote(payload: MojaloopQuoteRequest):
    """Acknowledge a quote request.

    This is an acknowledgement only: no quoting logic, no pricing and no
    persistence are implemented behind it.
    """
    return {
        "success": True,
        "message": "Quote received and acknowledged (no quoting logic implemented).",
        "quoteId": payload.quoteId,
        "transactionId": payload.transactionId,
    }


@router.post(
    "/transactionRequests", dependencies=[Depends(verify_webhook_signature)]
)
async def receive_transaction_request(payload: MojaloopTransactionRequest):
    """Acknowledge a transaction request. Acknowledgement only; nothing is stored."""
    return {
        "success": True,
        "message": "Transaction request received and acknowledged (not persisted).",
        "transactionRequestId": payload.transactionRequestId,
    }


@router.get("/status")
def status():
    """Integration configuration and hub reachability."""
    return {
        "webhook_listener": "running",
        "signature_verification": bool(settings.security.webhook_secret),
        "integration": connection_status(),
    }


@router.get("/transfers/{transaction_id}/verify")
async def verify_single_transfer(transaction_id: str, db: Session = Depends(get_db)):
    """Verify one stored transfer against the hub's own record of it.

    This replaces the previous bulk pull. FSPIOP exposes no collection endpoint
    for transfers — only ``GET /transfers/{ID}`` — so verification is per
    transfer. Transfers themselves arrive by push on ``POST /webhooks/transfers``.

    A disagreement in amount, currency or state is reported, not corrected: a
    divergence between our copy and the hub's is itself a finding.
    """
    return await run_in_threadpool(verify_transfer, db, transaction_id)


@router.post("/simulate-inbound")
async def simulate_inbound(db: Session = Depends(get_db)):
    """Inject the built-in offline sample transfers as though pushed by the hub.

    Provided so the pipeline is demonstrable with no toolkit running. It is the
    same code path a real webhook takes, and the records are tagged
    ``source=mojaloop-sim`` so simulated data is never mistaken for live data.
    Prefer driving the real flow with the Testing Toolkit collection in
    ``ml-testing-toolkit/spec_files/collections``.
    """
    payload = simulated_transfers()
    result = await run_in_threadpool(
        persist_transfers, db, payload["transactions"], "mojaloop-sim"
    )
    body = result.as_dict()
    body["simulated"] = True
    body["note"] = (
        "Injected locally. For a real Mojaloop flow, run the Testing Toolkit "
        "collection which POSTs transfers to /webhooks/transfers."
    )
    return body


@router.get("/simulate-fetch")
def simulate_fetch():
    """Built-in offline sample payload. Always simulated; never a live hub read."""
    return simulated_transfers()
