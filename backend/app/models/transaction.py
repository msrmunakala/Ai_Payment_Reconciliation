from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime
from app.database.db import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(String, unique=True, index=True, nullable=False)
    reference_id = Column(String, index=True, nullable=True)
    payer = Column(String, nullable=False)
    payee = Column(String, nullable=False)
    payer_fsp = Column(String, nullable=False)
    payee_fsp = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="USD")
    status = Column(String, default="PENDING")
    transfer_state = Column(String, default="RECEIVED")
    created_at = Column(DateTime, default=datetime.utcnow)
