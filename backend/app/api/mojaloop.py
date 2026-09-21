from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.schemas.mojaloop import MojaloopTransfer, MojaloopQuoteRequest, MojaloopTransactionRequest
from app.services.reconciliation_service import process_transfer
from app.services.mojaloop_service import fetch_transactions

router = APIRouter(
    prefix="/webhooks",
    tags=["Mojaloop Webhooks"]
)


@router.post("/transfers")
async def receive_transfer(
    transfer: MojaloopTransfer,
    db: Session = Depends(get_db)
):
    result = process_transfer(db, transfer)
    return {
        "success": True,
        "data": result
    }


@router.post("/quotes")
async def receive_quote(payload: MojaloopQuoteRequest):
    return {
        "success": True,
        "message": "Quote received and validated",
        "payload": payload
    }


@router.post("/transactionRequests")
async def receive_transaction_request(payload: MojaloopTransactionRequest):
    return {
        "success": True,
        "message": "Transaction Request received and validated",
        "payload": payload
    }


@router.get("/status")
async def status():
    return {
        "status": "Webhook Listener Running",
        "mojaloop_spec": "v1.1"
    }


@router.get("/simulate-fetch")
async def simulate_fetch():
    return fetch_transactions()