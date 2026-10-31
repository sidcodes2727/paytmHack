"""Mock bank / payment-gateway / merchant ledger: the *trusted* external system of record.

Write access (refund API, bank webhook) is only available through `MockBank`.
The Proof-of-Done verifier gets `ReadOnlyLedger`, which opens the SQLite file with
mode=ro and requires a `role:independent_auditor` capability token.
"""
import hashlib
import sqlite3
from typing import Any

import clock
import security
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS mock_txn (
  txn_id TEXT PRIMARY KEY, customer_id TEXT, tx_type TEXT, verified_amount REAL,
  txn_timestamp TEXT, gateway_status TEXT, bank_status TEXT, merchant_status TEXT,
  bank_webhook_received INTEGER DEFAULT 0, dispute_history_count INTEGER DEFAULT 0,
  failure_stage TEXT, counterparty TEXT, refund_status TEXT DEFAULT 'NONE', refund_ref TEXT,
  refund_amount REAL, utr TEXT, utr_status TEXT, settled_at TEXT, receipt_sig TEXT,
  merchant_id TEXT, rrn TEXT
);
"""


def _conn(readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    else:
        c = sqlite3.connect(DB_PATH, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
    c.row_factory = sqlite3.Row
    return c


def init_schema() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)


def _row(c, txn_id) -> dict[str, Any] | None:
    r = c.execute("SELECT * FROM mock_txn WHERE txn_id=?", (txn_id,)).fetchone()
    return dict(r) if r else None


class MockBank:
    """Read/write simulator of the external rails (what the real world does)."""

    def reset(self) -> None:
        with _conn() as c:
            c.executescript("DROP TABLE IF EXISTS mock_txn;" + SCHEMA)

    def seed(self, rec: dict) -> None:
        cols = ",".join(rec)
        with _conn() as c:
            c.execute(f"INSERT OR REPLACE INTO mock_txn ({cols}) VALUES ({','.join('?' * len(rec))})",
                      list(rec.values()))

    def get(self, txn_id: str) -> dict | None:
        with _conn() as c:
            return _row(c, txn_id)

    def list_for_customer(self, customer_id: str) -> list[dict]:
        with _conn() as c:
            rows = c.execute("SELECT * FROM mock_txn WHERE customer_id=? ORDER BY txn_timestamp DESC", (customer_id,)).fetchall()
        return [dict(r) for r in rows]

    def find_by_rrn(self, rrn: str) -> dict | None:
        """The payment's own 12-digit UPI reference (shown in every UPI app), not the refund UTR."""
        with _conn() as c:
            r = c.execute("SELECT * FROM mock_txn WHERE rrn=?", (rrn,)).fetchone()
        return dict(r) if r else None

    def initiate_refund(self, txn_id: str, amount: float, idem_key: str) -> dict:
        """Refund API. Always answers 202 Accepted: acceptance is NOT settlement."""
        with _conn() as c:
            r = _row(c, txn_id)
            if not r:
                return {"status_code": 404, "error": "txn not found"}
            ref = "RFD" + hashlib.sha256(idem_key.encode()).hexdigest()[:10].upper()
            c.execute("UPDATE mock_txn SET refund_status='PENDING_BANK', refund_ref=?, refund_amount=? WHERE txn_id=?",
                      (ref, amount, txn_id))
        return {"status_code": 202, "status": "Accepted", "refund_ref": ref, "amount": amount,
                "note": "Accepted for processing. Settlement not yet confirmed."}

    def bank_webhook_settle(self, txn_id: str) -> dict:
        """Simulates the bank switch posting a settled credit with a 12-digit UTR."""
        with _conn() as c:
            r = _row(c, txn_id)
            if not r:
                return {"ok": False, "error": "txn not found"}
            if r["refund_status"] != "PENDING_BANK":
                return {"ok": False, "error": f"no pending refund (refund_status={r['refund_status']})"}
            digits = str(int(hashlib.sha256((txn_id + (r["refund_ref"] or "")).encode()).hexdigest(), 16))[:12]
            utr = digits.zfill(12)
            settled_at = clock.now().isoformat()
            body = {"txn_id": txn_id, "utr": utr, "amount": r["refund_amount"],
                    "status": "CREDITED", "settled_at": settled_at}
            sig = security.sign_bank_receipt(body)
            c.execute("""UPDATE mock_txn SET bank_webhook_received=1, utr=?, utr_status='CREDITED',
                         refund_status='SETTLED', settled_at=?, receipt_sig=? WHERE txn_id=?""",
                      (utr, settled_at, sig, txn_id))
        return {"ok": True, "receipt": body, "signature": sig}


class ReadOnlyLedger:
    """Read-only view for the independent auditor (separate ro SQLite connection)."""

    def __init__(self, capability_token: str):
        if security.verify_capability(capability_token, "role:independent_auditor") is None:
            raise PermissionError("ReadOnlyLedger requires role:independent_auditor")

    def get(self, txn_id: str) -> dict | None:
        c = _conn(readonly=True)
        try:
            return _row(c, txn_id)
        finally:
            c.close()


bank = MockBank()
