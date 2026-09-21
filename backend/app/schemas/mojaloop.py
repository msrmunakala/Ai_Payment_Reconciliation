from typing import Optional, Dict, Any
from pydantic import BaseModel


class MojaloopTransfer(BaseModel):
    transactionId: str
    payer: str
    payee: str
    payerFsp: str
    payeeFsp: str
    amount: float
    currency: str = "USD"
    transferState: str = "COMMITTED"
    referenceId: Optional[str] = None


class MojaloopQuoteRequest(BaseModel):
    quoteId: str
    transactionId: str
    payee: Dict[str, Any]
    payer: Dict[str, Any]
    amountType: str = "SEND"
    amount: Dict[str, Any]
    transactionType: Dict[str, Any]


class MojaloopTransactionRequest(BaseModel):
    transactionRequestId: str
    payee: Dict[str, Any]
    payer: Dict[str, Any]
    amount: Dict[str, Any]
    transactionType: Dict[str, Any]