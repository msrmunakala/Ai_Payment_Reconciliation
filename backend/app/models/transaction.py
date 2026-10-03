from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text

from app.core.constants import TransactionStatus, TransferState
from app.database.db import Base


class Transaction(Base):
    """A Mojaloop-side transfer: the record we reconcile *from*.

    New in this revision:

    * ``amount_base`` / ``base_currency`` / ``fx_rate_used`` persist the
      normalised value instead of recomputing FX on every comparison. Storing
      the rate alongside the converted amount is what makes a historical
      reconciliation reproducible.
    * ``source``, ``raw_payload`` and ``idempotency_key`` carry ingestion
      provenance, so a record pulled from the Mojaloop API can be traced back to
      the exact payload and re-ingested safely.
    * Indexes on the columns the matching engine actually blocks on
      (``amount_base``, ``created_at``, ``currency``).
    """

    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(String, unique=True, index=True, nullable=False)
    reference_id = Column(String, index=True, nullable=True)
    payer = Column(String, nullable=False)
    payee = Column(String, nullable=False)
    payer_fsp = Column(String, nullable=False)
    payee_fsp = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="USD", index=True)
    status = Column(String, default=TransactionStatus.PENDING.value, index=True)
    transfer_state = Column(String, default=TransferState.RECEIVED.value)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # --- normalised (unified-schema) values ---
    amount_base = Column(Float, nullable=True, index=True)
    base_currency = Column(String, nullable=True)
    fx_rate_used = Column(Float, nullable=True)
    normalized_at = Column(DateTime, nullable=True)

    # --- ingestion provenance ---
    source = Column(String, default="api", index=True)  # api / mojaloop / upload / seed
    source_batch_id = Column(String, nullable=True, index=True)
    idempotency_key = Column(String, nullable=True, index=True)
    raw_payload = Column(Text, nullable=True)
    ingested_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # Composite index supporting the amount-window + date-window blocking
        # strategy used by the matching engine.
        Index("ix_transactions_base_amount_date", "amount_base", "created_at"),
        Index("ix_transactions_currency_created", "currency", "created_at"),
    )
