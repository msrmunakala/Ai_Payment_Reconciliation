import os
import uuid

# Live Mojaloop hub and bank settlement HTTP clients are stubs until real
# credentials exist. Uncomment and populate via environment — never hard-code secrets.
#
# MOJALOOP_BASE_URL = os.getenv("MOJALOOP_BASE_URL", "")
# MOJALOOP_API_KEY = os.getenv("MOJALOOP_API_KEY", "")
# BANK_SETTLEMENT_BASE_URL = os.getenv("BANK_SETTLEMENT_BASE_URL", "")
# BANK_SETTLEMENT_API_KEY = os.getenv("BANK_SETTLEMENT_API_KEY", "")
# CENTRAL_BANK_RTGS_API_KEY = os.getenv("CENTRAL_BANK_RTGS_API_KEY", "")
#
# def fetch_hub_transfers():
#     import httpx
#     response = httpx.get(
#         f"{MOJALOOP_BASE_URL}/transfers",
#         headers={"X-API-Key": MOJALOOP_API_KEY, "FSPIOP-Source": os.getenv("MOJALOOP_DFSP_ID", "")},
#         timeout=30,
#     )
#     response.raise_for_status()
#     return response.json()
#
# def fetch_bank_settlements(account_id: str, value_date: str):
#     import httpx
#     response = httpx.get(
#         f"{BANK_SETTLEMENT_BASE_URL}/statements",
#         headers={"Authorization": f"Bearer {BANK_SETTLEMENT_API_KEY}"},
#         params={"accountId": account_id, "valueDate": value_date},
#         timeout=30,
#     )
#     response.raise_for_status()
#     return response.json()


def fetch_transactions():
    return {
        "source": "file-upload-demo",
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
