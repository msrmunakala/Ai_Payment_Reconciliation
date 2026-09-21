from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from app.database.db import Base


class Reconciliation(Base):
    __tablename__ = "reconciliations"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(String, index=True, nullable=False)
    ledger_id = Column(String, index=True, nullable=True)  # Bank/ERP ID matched against
    ledger_type = Column(String, default="BANK")  # BANK / ERP / MOJALOOP
    status = Column(String, default="unmatched")  # matched / partial / unmatched / flag_for_review
    amount_difference = Column(Float, default=0.0)
    match_confidence = Column(Float, default=0.0)  # 0 to 100 percentage
    confidence_tier = Column(String, default="low")  # high / medium / low
    remarks = Column(Text, nullable=True)
    reconciled_at = Column(DateTime, default=datetime.utcnow)


class Anomaly(Base):
    __tablename__ = "anomalies"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(String, index=True, nullable=False)
    anomaly_type = Column(String, nullable=False)  # duplicate_charge / amount_mismatch / missing_settlement / timing_anomaly / unrecognized_account
    severity = Column(String, default="medium")  # high / medium / low
    risk_score = Column(Float, default=0.5)  # 0.0 to 1.0
    explanation = Column(Text, nullable=False)
    detected_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="OPEN")  # OPEN / RESOLVED / IGNORED


class CashFlowForecast(Base):
    __tablename__ = "cash_flow_forecasts"

    id = Column(Integer, primary_key=True, index=True)
    forecast_date = Column(DateTime, nullable=False)
    predicted_amount = Column(Float, nullable=False)
    lower_bound = Column(Float, nullable=False)
    upper_bound = Column(Float, nullable=False)
    inflow = Column(Float, default=0.0)
    outflow = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)


class FXRate(Base):
    __tablename__ = "fx_rates"

    id = Column(Integer, primary_key=True, index=True)
    from_currency = Column(String, nullable=False, index=True)
    to_currency = Column(String, nullable=False, index=True)
    rate = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow)
