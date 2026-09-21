from sqlalchemy.orm import Session

from app.models.transaction import Transaction


def process_transfer(db: Session, transfer):

    transaction = (
        db.query(Transaction)
        .filter(Transaction.transaction_id == transfer.transactionId)
        .first()
    )

    if transaction:

        transaction.status = "SUCCESS"
        transaction.transfer_state = transfer.transferState

        db.commit()
        db.refresh(transaction)

        return {
            "transaction_id": transaction.transaction_id,
            "status": transaction.status,
            "transfer_state": transaction.transfer_state,
            "message": "Transaction Reconciled Successfully"
        }

    return {
        "transaction_id": transfer.transactionId,
        "message": "Transaction Not Found"
    }