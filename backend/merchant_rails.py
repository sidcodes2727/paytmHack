"""Mock merchant systems + the connector layer that makes merchant refunds generic.

Every merchant keeps orders in its own raw format (a rail PNR, a food order, a recharge, a shop order). A connector
normalises that into `MerchantOrder`, the only shape the claim verifier reads, so the verification logic never knows
what a "PNR" is. Adding a merchant = registering one connector with a status map.

Merchant records are trusted facts from the merchant's system of record: nothing the customer says changes them.
Writes (refund request, refund settlement webhook) only happen here; the verifier gets `ReadOnlyMerchantLedger`.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Optional

from pydantic import BaseModel

import clock
import security
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS merchant_order (
  order_ref TEXT PRIMARY KEY, merchant_id TEXT, payment_txn_id TEXT, raw TEXT,
  refund_status TEXT DEFAULT 'NONE', refund_ref TEXT, refund_amount REAL, refund_utr TEXT,
  refund_settled_at TEXT, refund_sig TEXT, chargeback_status TEXT DEFAULT 'NONE'
);
"""


class Refund(BaseModel):
    refund_ref: str
    amount: float
    status: str                      # PENDING | CREDITED
    utr: Optional[str] = None
    credited_at: Optional[str] = None


class MerchantOrder(BaseModel):
    """Normalised order: identical for every merchant and category."""
    merchant_id: str
    merchant_name: str
    category: str                    # TRAVEL | FOOD | ECOMMERCE | RECHARGE | SUBSCRIPTION
    order_label: str                 # what the customer calls it: PNR, Order ID, Recharge ID
    order_ref: str
    payment_txn_id: str              # must be the customer's own payment
    customer_ref: str                # who the order belongs to (merchant's account mapping)
    amount: float
    status: str                      # FULFILLED | CANCELLED | NOT_DELIVERED | RETURNED | FAILED_FULFILMENT | PENDING
    cancelled_by: Optional[str]      # MERCHANT | CUSTOMER | SYSTEM
    status_at: str
    refundable_amount: float         # merchant-computed (after any charges)
    description: str
    refunds: list[Refund] = []
    chargeback_open: bool = False


# ------------------------------------------------------------------ connectors
class Connector:
    """Raw merchant record -> MerchantOrder. Field names and status vocabularies differ per merchant."""

    def __init__(self, merchant_id: str, name: str, category: str, order_label: str, ref_pattern: str,
                 fields: dict, statuses: dict, describe, context: str | None = None):
        self.merchant_id, self.name, self.category, self.order_label = merchant_id, name, category, order_label
        self.ref_re = re.compile(ref_pattern, re.I)
        # Bare-digit formats (a 10-digit PNR looks like a phone number) only count when the customer names them.
        self.context_re = re.compile(context, re.I) if context else None
        self.f, self.statuses, self.describe = fields, statuses, describe

    def normalise(self, raw: dict, row: dict) -> MerchantOrder:
        f = self.f
        status, by = self.statuses[raw[f["status"]]]
        refunds = []
        if row.get("refund_ref"):
            refunds.append(Refund(refund_ref=row["refund_ref"], amount=row["refund_amount"],
                                  status="CREDITED" if row["refund_status"] == "SETTLED" else "PENDING",
                                  utr=row.get("refund_utr"), credited_at=row.get("refund_settled_at")))
        amount = float(raw[f["amount"]])
        return MerchantOrder(
            merchant_id=self.merchant_id, merchant_name=self.name, category=self.category, order_label=self.order_label,
            order_ref=raw[f["ref"]], payment_txn_id=row["payment_txn_id"], customer_ref=raw[f["customer"]],
            amount=amount, status=status, cancelled_by=by, status_at=raw[f["updated"]],
            refundable_amount=float(raw.get(f.get("refundable", ""), amount) if status != "FULFILLED" else 0),
            description=self.describe(raw), refunds=refunds, chargeback_open=row.get("chargeback_status") == "OPEN")


CONNECTORS = {c.merchant_id: c for c in [
    Connector("RAILBOOK", "RailBook", "TRAVEL", "PNR", r"\b\d{10}\b",
              {"ref": "pnr", "customer": "passenger_account", "amount": "fare", "status": "booking_status",
               "updated": "status_changed_at", "refundable": "refund_due"},
              {"CONFIRMED": ("PENDING", None), "JOURNEY_COMPLETED": ("FULFILLED", None),
               "CANCELLED_BY_RAILWAY": ("CANCELLED", "MERCHANT"), "CANCELLED_BY_PASSENGER": ("CANCELLED", "CUSTOMER"),
               "CHART_NOT_PREPARED_WL": ("CANCELLED", "SYSTEM")},
              lambda r: f"Train {r['train']} · journey {r['journey_date']}", context=r"pnr|ticket|booking|train"),
    Connector("FOODNOW", "FoodNow", "FOOD", "Order ID", r"\bFN-?\d{4,}\b",
              {"ref": "order_id", "customer": "user_id", "amount": "total", "status": "state", "updated": "updated_at",
               "refundable": "refundable"},
              {"DELIVERED": ("FULFILLED", None), "UNDELIVERED": ("NOT_DELIVERED", None),
               "CANCELLED_BY_RESTAURANT": ("CANCELLED", "MERCHANT"), "CANCELLED_BY_USER": ("CANCELLED", "CUSTOMER")},
              lambda r: f"{r['restaurant']} order"),
    Connector("QUICKRECHARGE", "QuickRecharge", "RECHARGE", "Recharge ID", r"\bRC-?\d{4,}\b",
              {"ref": "recharge_id", "customer": "account", "amount": "amount", "status": "operator_status",
               "updated": "updated_at"},
              {"SUCCESS": ("FULFILLED", None), "FAILED": ("FAILED_FULFILMENT", "SYSTEM"), "PROCESSING": ("PENDING", None)},
              lambda r: f"Mobile recharge {r['mobile']}"),
    Connector("SHOPKART", "ShopKart", "ECOMMERCE", "Order No.", r"\bSK-?\d{4,}\b",
              {"ref": "order_no", "customer": "buyer", "amount": "amount", "status": "shipment", "updated": "updated_at",
               "refundable": "refundable"},
              {"DELIVERED": ("FULFILLED", None), "RETURN_RECEIVED": ("RETURNED", "CUSTOMER"),
               "LOST_IN_TRANSIT": ("NOT_DELIVERED", None), "CANCELLED": ("CANCELLED", "MERCHANT"),
               "IN_TRANSIT": ("PENDING", None)},
              lambda r: r["item"]),
]}


# ------------------------------------------------------------------ storage
def _conn(readonly: bool = False) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10) if readonly else sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def reset() -> None:
    with _conn() as c:
        c.executescript("DROP TABLE IF EXISTS merchant_order;" + SCHEMA)


def seed(merchant_id: str, payment_txn_id: str, raw: dict, refund: dict | None = None, chargeback: bool = False) -> None:
    ref = raw[CONNECTORS[merchant_id].f["ref"]]
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO merchant_order (order_ref, merchant_id, payment_txn_id, raw, chargeback_status) "
                  "VALUES (?,?,?,?,?)", (ref, merchant_id, payment_txn_id, json.dumps(raw), "OPEN" if chargeback else "NONE"))
    if refund:                         # history: the merchant already refunded (or started refunding) this order
        request_refund(ref, refund["amount"], f"seed:{ref}")
        if refund.get("settled"):
            settle_refund(ref, at=refund.get("at"))


def _row(c, order_ref: str) -> dict | None:
    r = c.execute("SELECT * FROM merchant_order WHERE order_ref=?", (order_ref,)).fetchone()
    return dict(r) if r else None


def _norm(row: dict | None) -> MerchantOrder | None:
    if not row:
        return None
    return CONNECTORS[row["merchant_id"]].normalise(json.loads(row["raw"]), row)


def has_connector(merchant_id: str | None) -> bool:
    return bool(merchant_id) and merchant_id in CONNECTORS


def order_for_payment(txn_id: str) -> MerchantOrder | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM merchant_order WHERE payment_txn_id=?", (txn_id,)).fetchone()
    return _norm(dict(r) if r else None)


def order_by_ref(order_ref: str) -> MerchantOrder | None:
    with _conn() as c:
        return _norm(_row(c, order_ref))


def refs_in_text(text: str) -> list[dict]:
    """Order references the customer mentioned, in any connector's format. Unknown refs are kept: a well-formed PNR
    that no merchant knows about is itself evidence."""
    out, seen = [], set()
    for con in CONNECTORS.values():
        if con.context_re and not con.context_re.search(text or ""):
            continue
        for m in con.ref_re.finditer(text or ""):
            tok = m.group(0).upper()
            if re.fullmatch(r"[A-Z]+\d+", tok):
                tok = re.sub(r"^([A-Z]+)(\d+)$", r"\1-\2", tok)
            if tok in seen:
                continue
            seen.add(tok)
            o = order_by_ref(tok)
            out.append({"ref": tok, "merchant_id": con.merchant_id, "label": con.order_label,
                        "found": o is not None, "payment_txn_id": o.payment_txn_id if o else None})
    return out


# ------------------------------------------------------------------ merchant refund API (write side)
def request_refund(order_ref: str, amount: float, idem_key: str) -> dict:
    """Merchant refund API. Answers 202: the merchant accepted the request, the money has not moved yet."""
    with _conn() as c:
        if not _row(c, order_ref):
            return {"status_code": 404, "error": "order not found"}
        ref = "MRF" + hashlib.sha256(idem_key.encode()).hexdigest()[:10].upper()
        c.execute("UPDATE merchant_order SET refund_status='PENDING', refund_ref=?, refund_amount=? WHERE order_ref=?",
                  (ref, amount, order_ref))
    return {"status_code": 202, "status": "Accepted", "refund_ref": ref, "amount": amount,
            "note": "Merchant accepted the refund request. Credit not yet confirmed."}


def receipt_body(row: dict) -> dict:
    return {"order_ref": row["order_ref"], "payment_txn_id": row["payment_txn_id"], "utr": row["refund_utr"],
            "amount": row["refund_amount"], "status": "CREDITED" if row["refund_status"] == "SETTLED" else row["refund_status"],
            "settled_at": row["refund_settled_at"]}


def settle_refund(order_ref: str, at: str | None = None) -> dict:
    """Simulates the refund reaching the customer's bank: settled 12-digit UTR + bank-signed receipt."""
    with _conn() as c:
        r = _row(c, order_ref)
        if not r:
            return {"ok": False, "error": "order not found"}
        if r["refund_status"] != "PENDING":
            return {"ok": False, "error": f"no pending refund (refund_status={r['refund_status']})"}
        utr = str(int(hashlib.sha256((order_ref + r["refund_ref"]).encode()).hexdigest(), 16))[:12].zfill(12)
        r.update(refund_status="SETTLED", refund_utr=utr, refund_settled_at=at or clock.now().isoformat())
        body = receipt_body(r)
        sig = security.sign_bank_receipt(body)
        c.execute("UPDATE merchant_order SET refund_status='SETTLED', refund_utr=?, refund_settled_at=?, refund_sig=? "
                  "WHERE order_ref=?", (utr, r["refund_settled_at"], sig, order_ref))
    return {"ok": True, "receipt": body, "signature": sig}


class ReadOnlyMerchantLedger:
    """Read-only view for the independent auditor (separate mode=ro SQLite connection)."""

    def __init__(self, capability_token: str):
        if security.verify_capability(capability_token, "role:independent_auditor") is None:
            raise PermissionError("ReadOnlyMerchantLedger requires role:independent_auditor")

    def get(self, order_ref: str) -> dict | None:
        c = _conn(readonly=True)
        try:
            return _row(c, order_ref)
        finally:
            c.close()
