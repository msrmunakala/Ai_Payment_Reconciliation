import random
import uuid
from datetime import datetime, timedelta
from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.models.transaction import Transaction
from app.models.bank_transaction import BankTransaction, ERPRecord
from app.models.reconciliation import Reconciliation, Anomaly, CashFlowForecast, FXRate
from app.services.fx_service import convert_to_usd, init_fx_rates
from app.services.forecasting_service import generate_forecast


def run_reconciliation(db: Session):
    # Fetch un-reconciled or all transactions across ledgers
    transactions = db.query(Transaction).all()
    bank_records = db.query(BankTransaction).all()
    erp_records = db.query(ERPRecord).all()

    # Clear old results to support fresh run
    db.query(Reconciliation).delete()
    db.query(Anomaly).delete()
    db.commit()

    reconciled_count = 0
    matched_count = 0
    anomaly_count = 0

    # Track matched ledger records to detect duplicate charges or missing settlements
    matched_bank_ids = set()
    matched_erp_ids = set()
    seen_amounts = {}

    for tx in transactions:
        tx_usd_amount = convert_to_usd(tx.amount, tx.currency, db)
        best_match = None
        best_confidence = 0.0
        match_type = None
        ledger_id = None
        amount_diff = 0.0

        # Check duplicate charge anomaly
        amount_key = (tx.payer, tx.amount, tx.currency)
        if amount_key in seen_amounts:
            prev_tx_id = seen_amounts[amount_key]
            anomaly = Anomaly(
                transaction_id=tx.transaction_id,
                anomaly_type="duplicate_charge",
                severity="high",
                risk_score=0.92,
                explanation=f"Potential duplicate charge detected. Identical amount {tx.currency} {tx.amount} and payer '{tx.payer}' matches previous transaction '{prev_tx_id}'."
            )
            db.add(anomaly)
            anomaly_count += 1
        else:
            seen_amounts[amount_key] = tx.transaction_id

        # 1. Try matching against Bank Records
        for bank in bank_records:
            if bank.bank_statement_id in matched_bank_ids:
                continue

            bank_usd_amount = convert_to_usd(bank.amount, bank.currency, db)
            amount_delta = abs(tx_usd_amount - bank_usd_amount)

            # String similarity on counterparty & payer/payee
            name_score_1 = fuzz.token_sort_ratio(tx.payer.lower(), bank.counterparty.lower())
            name_score_2 = fuzz.token_sort_ratio(tx.payee.lower(), bank.counterparty.lower())
            name_score = max(name_score_1, name_score_2)

            # Reference ID match boost
            ref_boost = 0.0
            if tx.reference_id and bank.reference_number and tx.reference_id.strip() == bank.reference_number.strip():
                ref_boost = 40.0

            # Calculate composite confidence score (0 to 100)
            amount_score = max(0.0, 100.0 - (amount_delta / (tx_usd_amount + 1e-5)) * 100.0)
            confidence = (name_score * 0.4) + (amount_score * 0.4) + ref_boost

            if confidence > best_confidence:
                best_confidence = min(100.0, round(confidence, 2))
                best_match = bank
                match_type = "BANK"
                ledger_id = bank.bank_statement_id
                amount_diff = round(amount_delta, 2)

        # 2. Try matching against ERP Records if no high bank match
        if best_confidence < 85.0:
            for erp in erp_records:
                if erp.erp_id in matched_erp_ids:
                    continue

                erp_usd_amount = convert_to_usd(erp.expected_amount, erp.currency, db)
                amount_delta = abs(tx_usd_amount - erp_usd_amount)

                name_score = fuzz.token_sort_ratio(tx.payee.lower(), erp.customer_vendor_name.lower())
                ref_boost = 40.0 if (tx.reference_id and erp.invoice_number and tx.reference_id.strip() == erp.invoice_number.strip()) else 0.0
                amount_score = max(0.0, 100.0 - (amount_delta / (tx_usd_amount + 1e-5)) * 100.0)

                confidence = (name_score * 0.4) + (amount_score * 0.4) + ref_boost

                if confidence > best_confidence:
                    best_confidence = min(100.0, round(confidence, 2))
                    best_match = erp
                    match_type = "ERP"
                    ledger_id = erp.erp_id
                    amount_diff = round(amount_delta, 2)

        # Determine Tier & Status
        if best_confidence >= 90.0:
            tier = "high"
            status = "matched"
            remarks = f"Automated AI High-Confidence Match against {match_type} ledger record '{ledger_id}'."
            matched_count += 1
            if match_type == "BANK":
                matched_bank_ids.add(ledger_id)
            elif match_type == "ERP":
                matched_erp_ids.add(ledger_id)
        elif best_confidence >= 70.0:
            tier = "medium"
            status = "partial"
            remarks = f"Partial Match against {match_type} ledger record '{ledger_id}'. Slight amount difference of {amount_diff} USD or name variance."
            
            # Record amount mismatch anomaly
            anomaly = Anomaly(
                transaction_id=tx.transaction_id,
                anomaly_type="amount_mismatch",
                severity="medium",
                risk_score=0.55,
                explanation=f"Amount difference of ${amount_diff} USD between Mojaloop transaction ({tx.currency} {tx.amount}) and ledger record '{ledger_id}'."
            )
            db.add(anomaly)
            anomaly_count += 1
        else:
            tier = "low"
            status = "unmatched"
            remarks = "No matching bank statement or ERP invoice record found within acceptable confidence threshold."
            
            anomaly = Anomaly(
                transaction_id=tx.transaction_id,
                anomaly_type="missing_settlement",
                severity="high",
                risk_score=0.88,
                explanation=f"Mojaloop transfer of {tx.currency} {tx.amount} has no matching settlement record in central bank or ERP ledgers."
            )
            db.add(anomaly)
            anomaly_count += 1

        rec = Reconciliation(
            transaction_id=tx.transaction_id,
            ledger_id=ledger_id,
            ledger_type=match_type or "BANK",
            status=status,
            amount_difference=amount_diff,
            match_confidence=best_confidence,
            confidence_tier=tier,
            remarks=remarks
        )
        db.add(rec)
        reconciled_count += 1

    db.commit()

    # Generate updated cash flow forecast
    generate_forecast(db, days=7)

    return {
        "status": "success",
        "total_reconciled": reconciled_count,
        "matched_count": matched_count,
        "anomalies_detected": anomaly_count,
        "message": f"AI Reconciliation complete. {matched_count}/{reconciled_count} matched successfully."
    }


def seed_demo_data(db: Session, count: int = 150):
    # Clear existing data
    db.query(Reconciliation).delete()
    db.query(Anomaly).delete()
    db.query(CashFlowForecast).delete()
    db.query(Transaction).delete()
    db.query(BankTransaction).delete()
    db.query(ERPRecord).delete()
    db.commit()

    init_fx_rates(db)

    # Corporate Entity Pools
    payers = ["Acme Corporation", "Apex Retailers", "Nairobi Enterprises", "Euro Import Co", "Oceanic Shipping", "Delta Innovations", "Sun Energy Ltd", "FastPay Merchant", "Zenith Solutions", "Nexus Logistics", "Horizon Traders", "Orion Systems", "Sterling Pharma", "Vanguard Energy", "Global Telecom", "Atlas Manufacturing", "Cobalt Dynamics"]
    payees = ["Global Logistics Ltd", "TechSupply Pvt Ltd", "Safari Ventures", "Berlin Distro GmbH", "Port Terminal Services", "Omega Softwares", "Grid Power Corp", "Supermarket Retail", "CloudNet Services", "Freightways Global", "Apex Wholesale", "Quantum IT", "Premier Supplies", "Eco Power Systems", "InterState Connect", "SteelWorks Inc"]
    fsp_list = ["BankA", "BankB", "MobileMoneyX", "PayCentral", "DFSP-Alpha"]
    currencies = ["USD", "INR", "EUR", "KES"]

    # 1. Base 10 core transactions
    base_txs = [
        ("TX-1001", "REF-8801", "Acme Corporation", "Global Logistics Ltd", 15400.0, "USD", "BankA", "BankB"),
        ("TX-1002", "REF-8802", "Apex Retailers", "TechSupply Pvt Ltd", 450000.0, "INR", "MobileMoneyX", "BankA"),
        ("TX-1003", "REF-8803", "Nairobi Enterprises", "Safari Ventures", 125000.0, "KES", "DFSP-Alpha", "PayCentral"),
        ("TX-1004", "REF-8804", "Euro Import Co", "Berlin Distro GmbH", 28900.0, "EUR", "BankB", "BankA"),
        ("TX-1005", "REF-8805", "Acme Corporation", "Global Logistics Ltd", 15400.0, "USD", "BankA", "BankB"),  # Intentional Duplicate
        ("TX-1006", "REF-8806", "Oceanic Shipping", "Port Terminal Services", 72300.0, "USD", "BankA", "DFSP-Alpha"),
        ("TX-1007", "REF-8807", "Delta Innovations", "Omega Softwares", 18500.0, "USD", "PayCentral", "BankB"),
        ("TX-1008", "REF-8808", "Sun Energy Ltd", "Grid Power Corp", 940000.0, "INR", "MobileMoneyX", "PayCentral"),
        ("TX-1009", "REF-8809", "Unrecognized Sender LLC", "Mystery Account Inc", 65000.0, "USD", "BankB", "DFSP-Alpha"),  # Intentional Unmatched
        ("TX-1010", "REF-8810", "FastPay Merchant", "Supermarket Retail", 8400.0, "USD", "PayCentral", "BankA"),
    ]

    for tx_id, ref_id, payer, payee, amt, curr, p_fsp, payee_fsp in base_txs:
        t = Transaction(
            transaction_id=tx_id,
            reference_id=ref_id,
            payer=payer,
            payee=payee,
            payer_fsp=p_fsp,
            payee_fsp=payee_fsp,
            amount=amt,
            currency=curr,
            status="SUCCESS",
            transfer_state="COMMITTED",
            created_at=datetime.utcnow() - timedelta(hours=random.randint(1, 48))
        )
        db.add(t)

    # Base Bank Records
    bank_txs = [
        ("BS-9001", "REF-8801", "Acme Corp", 15400.0, "USD"),
        ("BS-9002", "REF-8802", "Apex Retailers Ltd", 450000.0, "INR"),
        ("BS-9003", "REF-8803", "Nairobi Ent", 125000.0, "KES"),
        ("BS-9004", "REF-8804", "Euro Import Co", 28500.0, "EUR"),
        ("BS-9006", "REF-8806", "Oceanic Shipping", 72300.0, "USD"),
        ("BS-9007", "REF-8807", "Delta Innovations", 18500.0, "USD"),
        ("BS-9008", "REF-8808", "Sun Energy Limited", 940000.0, "INR"),
        ("BS-9010", "REF-8810", "FastPay Merchant", 8400.0, "USD"),
    ]

    for b_id, ref_id, counterparty, amt, curr in bank_txs:
        bt = BankTransaction(
            bank_statement_id=b_id,
            account_number="ACC-99042",
            counterparty=counterparty,
            amount=amt,
            currency=curr,
            transaction_type="CREDIT",
            reference_number=ref_id,
            bank_name="Central Settlement Bank"
        )
        db.add(bt)

    # Base ERP Records
    erp_recs = [
        ("ERP-5001", "REF-8801", "Global Logistics Ltd", 15400.0, "USD"),
        ("ERP-5002", "REF-8802", "TechSupply Pvt Ltd", 450000.0, "INR"),
        ("ERP-5003", "REF-8803", "Safari Ventures", 125000.0, "KES"),
        ("ERP-5004", "REF-8804", "Berlin Distro GmbH", 28900.0, "EUR"),
    ]

    for e_id, inv_num, name, amt, curr in erp_recs:
        erp = ERPRecord(
            erp_id=e_id,
            invoice_number=inv_num,
            customer_vendor_name=name,
            expected_amount=amt,
            currency=curr,
            ledger_account="1100-ACCOUNTS-RECEIVABLE"
        )
        db.add(erp)

    # 2. Dynamic generation up to requested count (e.g. 150 records)
    for i in range(11, count + 1):
        tx_id = f"TX-{1000 + i}"
        ref_id = f"REF-{8800 + i}"
        payer = random.choice(payers)
        payee = random.choice(payees)
        curr = random.choice(currencies)

        if curr == "USD":
            amt = round(random.uniform(2000.0, 85000.0), 2)
        elif curr == "INR":
            amt = round(random.uniform(150000.0, 2500000.0), 2)
        elif curr == "EUR":
            amt = round(random.uniform(5000.0, 60000.0), 2)
        else:
            amt = round(random.uniform(50000.0, 800000.0), 2)

        p_fsp = random.choice(fsp_list)
        payee_fsp = random.choice(fsp_list)

        t = Transaction(
            transaction_id=tx_id,
            reference_id=ref_id,
            payer=payer,
            payee=payee,
            payer_fsp=p_fsp,
            payee_fsp=payee_fsp,
            amount=amt,
            currency=curr,
            status="SUCCESS",
            transfer_state="COMMITTED",
            created_at=datetime.utcnow() - timedelta(hours=random.randint(1, 72))
        )
        db.add(t)

        # Generate corresponding bank statement matching record for EVERY transaction
        b_amt = amt if random.random() < 0.90 else round(amt * 0.98, 2)
        bt = BankTransaction(
            bank_statement_id=f"BS-{9000 + i}",
            account_number=f"ACC-{random.randint(10000, 99999)}",
            counterparty=payer,
            amount=b_amt,
            currency=curr,
            transaction_type="CREDIT",
            reference_number=ref_id,
            bank_name="Central Settlement Bank"
        )
        db.add(bt)

        # Generate corresponding ERP invoice record for EVERY transaction
        erp = ERPRecord(
            erp_id=f"ERP-{5000 + i}",
            invoice_number=ref_id,
            customer_vendor_name=payee,
            expected_amount=amt,
            currency=curr,
            ledger_account="1100-ACCOUNTS-RECEIVABLE"
        )
        db.add(erp)

    db.commit()

    # Execute initial reconciliation run
    run_reconciliation(db)

    return {"message": f"Demo dataset of {count} transactions successfully seeded and reconciled."}


def process_transfer(db: Session, transfer):
    # Check if transaction exists
    transaction = db.query(Transaction).filter(
        Transaction.transaction_id == transfer.transactionId
    ).first()

    if not transaction:
        transaction = Transaction(
            transaction_id=transfer.transactionId,
            reference_id=getattr(transfer, "referenceId", None) or f"REF-{uuid.uuid4().hex[:6].upper()}",
            payer=transfer.payer,
            payee=transfer.payee,
            payer_fsp=transfer.payerFsp,
            payee_fsp=transfer.payeeFsp,
            amount=transfer.amount,
            currency=getattr(transfer, "currency", "USD"),
            status="SUCCESS",
            transfer_state=transfer.transferState
        )
        db.add(transaction)
        db.commit()
        db.refresh(transaction)

    # Run quick reconciliation update for this transaction
    run_reconciliation(db)

    return {
        "transaction_id": transaction.transaction_id,
        "status": transaction.status,
        "transfer_state": transaction.transfer_state,
        "message": "Transaction Received and Reconciled Successfully"
    }

