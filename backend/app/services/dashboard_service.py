from sqlalchemy.orm import Session
from app.models.reconciliation import Reconciliation, Anomaly
from app.models.transaction import Transaction
from app.models.bank_transaction import BankTransaction, ERPRecord


def get_dashboard_summary(db: Session):
    reconciliations = db.query(Reconciliation).all()
    total_recs = len(reconciliations)
    
    matched = sum(1 for r in reconciliations if r.status == "matched")
    partial = sum(1 for r in reconciliations if r.status == "partial")
    unmatched = sum(1 for r in reconciliations if r.status == "unmatched")
    
    match_rate = round((matched / total_recs * 100), 2) if total_recs > 0 else 0.0
    
    anomaly_count = db.query(Anomaly).filter(Anomaly.status == "OPEN").count()
    
    all_txs = db.query(Transaction).all()
    total_value = round(sum(tx.amount for tx in all_txs), 2)
    
    return {
        "total_transactions": total_recs if total_recs > 0 else len(all_txs),
        "matched_transactions": matched,
        "partial_matched": partial,
        "unmatched_transactions": unmatched,
        "match_rate": match_rate,
        "anomaly_count": anomaly_count,
        "total_reconciled_value": total_value,
        "synthetic_data_notice": "ERP records are synthetic demo data, generated for prototype validation."
    }
