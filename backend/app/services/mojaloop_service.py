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


class PersistResult:
    """Small compatibility wrapper for the older router contract."""

    def __init__(self, payload):
        self.payload = payload

    def as_dict(self):
        return self.payload


def connection_status():
    """Return the current legal/demo-mode connection state for the hub."""
    return {
        "status": "offline",
        "configured": False,
        "mode": "stubbed",
        "message": "Mojaloop is running in local demo mode until real credentials are configured.",
        "hub_base_url": os.getenv("MOJALOOP_BASE_URL", ""),
    }


def simulated_transfers():
    """Compatibility alias for the older mock-transfer API."""
    payload = fetch_transactions()
    payload["source"] = "mojaloop-sim"
    payload["count"] = len(payload.get("transactions", []))
    return payload


def persist_transfers(db, transactions, source="mojaloop-sim"):
    """Compatibility shim that accepts the older service call signature."""
    items = list(transactions or [])
    return PersistResult({
        "source": source,
        "count": len(items),
        "transactions": items,
    })


def verify_transfer(db, transaction_id):
    """Compatibility stub for single-transfer verification requests."""
    return {
        "transaction_id": transaction_id,
        "verified": False,
        "status": "not_found",
        "message": "No live Mojaloop connection is configured in this demo environment.",
    }
