import uuid

def fetch_transactions():
    return {
        "source": "Mojaloop Testing Toolkit",
        "count": 2,
        "transactions": [
            {
                "transaction_id": str(uuid.uuid4()),
                "payer": "Alice",
                "payee": "Bob",
                "payer_fsp": "BankA",
                "payee_fsp": "BankB",
                "amount": 5000,
                "currency": "INR",
                "status": "SUCCESS",
                "transfer_state": "COMMITTED"
            },
            {
                "transaction_id": str(uuid.uuid4()),
                "payer": "John",
                "payee": "David",
                "payer_fsp": "BankX",
                "payee_fsp": "BankY",
                "amount": 12000,
                "currency": "INR",
                "status": "SUCCESS",
                "transfer_state": "COMMITTED"
            }
        ]
    }
