"""Stage 4 - isolated, read-only Proof-of-Done verifier (role:independent_auditor).

It never imports the write-side of the ledger (MockBank). It only sees a ReadOnlyLedger
(separate read-only SQLite connection) and decides whether a case may be closed.
"""
import re

import security
from mock_ledger import ReadOnlyLedger

UTR_RE = re.compile(r"^\d{12}$")


def check_record(rec: dict) -> dict:
    """Pure decision function on a ledger row (also used by the benchmark's closure check)."""
    checks = {
        "utr_present_12_digits": bool(rec.get("utr")) and bool(UTR_RE.match(str(rec.get("utr")))),
        "status_credited": rec.get("utr_status") == "CREDITED",
        "refund_settled": rec.get("refund_status") == "SETTLED",
        "amount_matches_refund": rec.get("refund_amount") is not None and 0 < rec["refund_amount"] <= rec.get("verified_amount", 0),
    }
    if not rec.get("_skip_signature"):
        body = {"txn_id": rec.get("txn_id"), "utr": rec.get("utr"), "amount": rec.get("refund_amount"),
                "status": rec.get("utr_status"), "settled_at": rec.get("settled_at")}
        checks["bank_receipt_signature_valid"] = security.verify_bank_receipt(body, rec.get("receipt_sig") or "")
    return {"closable": all(checks.values()), "checks": checks}


def verify(txn_id: str, auditor_token: str) -> dict:
    ledger = ReadOnlyLedger(auditor_token)
    rec = ledger.get(txn_id)
    if not rec:
        return {"closable": False, "status": "BLOCKED: TXN_NOT_FOUND", "checks": {}, "receipt": None}
    res = check_record(rec)
    res["status"] = "VERIFIED CLOSED" if res["closable"] else "BLOCKED: AWAITING SETTLED UTR"
    res["receipt"] = ({"txn_id": txn_id, "utr": rec["utr"], "amount": rec["refund_amount"], "status": rec["utr_status"],
                       "settled_at": rec["settled_at"], "signature": rec["receipt_sig"]} if res["closable"] else None)
    res["verified_by"] = "role:independent_auditor"
    return res


def verify_merchant_refund(order_ref: str, txn_id: str, expected_amount: float, auditor_token: str) -> dict:
    """Merchant-funded refund: closes only on a bank-signed, settled UTR for this order, linked to this payment,
    for the amount the merchant record says was owed."""
    from merchant_rails import ReadOnlyMerchantLedger, receipt_body
    rec = ReadOnlyMerchantLedger(auditor_token).get(order_ref)
    if not rec:
        return {"closable": False, "status": "BLOCKED: ORDER_NOT_FOUND", "checks": {}, "receipt": None}
    body = receipt_body(rec)
    checks = {
        "utr_present_12_digits": bool(rec.get("refund_utr")) and bool(UTR_RE.match(str(rec["refund_utr"]))),
        "status_credited": rec.get("refund_status") == "SETTLED",
        "linked_to_original_payment": rec.get("payment_txn_id") == txn_id,
        "amount_matches_entitlement": rec.get("refund_amount") is not None and abs(rec["refund_amount"] - expected_amount) < 0.5,
        "bank_receipt_signature_valid": security.verify_bank_receipt(body, rec.get("refund_sig") or ""),
    }
    ok = all(checks.values())
    return {"closable": ok, "checks": checks, "verified_by": "role:independent_auditor",
            "status": "VERIFIED CLOSED" if ok else "BLOCKED: AWAITING MERCHANT REFUND CREDIT",
            "receipt": {**body, "signature": rec["refund_sig"]} if ok else None}
