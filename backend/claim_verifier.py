"""General claim verification. A customer claim is split into separate facts, and each fact is checked against an
independent system of record:

    "I paid ₹X"                -> bank / UPI ledger                          (trust: HIGH)
    "for this order / ticket"  -> merchant record: payment link + owner        (trust: HIGH)
    "it was cancelled / not delivered / recharge failed"
                               -> merchant record: status + who cancelled     (trust: HIGH)
    "and I haven't been paid"  -> merchant refund records, chargebacks         (trust: HIGH)
    screenshot                 -> OCR, compared against the ledger             (trust: LOW, a hint)
    text / voice               -> only tells us where to look                  (UNTRUSTED)

The verdict comes only from HIGH-trust sources. The customer's own words can make a claim *fail* (when they
contradict the records) but can never make it pass. Nothing here is merchant-specific: it reads `MerchantOrder`.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import config
from merchant_rails import MerchantOrder, order_by_ref
from mock_ledger import bank
from untrusted_ingestion import ClaimedContext, extract_amounts, strip_refs

TRUST = {"BANK_LEDGER": "HIGH", "MERCHANT_RECORD": "HIGH", "CASE_HISTORY": "HIGH", "REFUND_POLICY": "POLICY",
         "SCREENSHOT": "LOW", "CUSTOMER_TEXT": "UNTRUSTED"}
SOURCE_LABEL = {"BANK_LEDGER": "Bank ledger", "MERCHANT_RECORD": "Merchant record", "CASE_HISTORY": "Claim history",
                "REFUND_POLICY": "Refund policy", "SCREENSHOT": "Screenshot (OCR)", "CUSTOMER_TEXT": "Customer's words"}

# What the customer says happened. Used only to compare with the merchant's record, never to grant a refund.
CLAIM_REASONS = {
    "CANCELLED": r"cancel|cancl|cncl|radd|rad ho|रद्द",
    "NOT_DELIVERED": r"deliver|order (nahi|nhi|ni) (aaya|aya|mila)|khana (nahi|nhi|ni) (aaya|aya|mila)|parcel (nahi|nhi)|"
                     r"not received|never (came|arrived)|undeliver",
    "RETURNED": r"\breturn|wapas bhej|wapas kar diya",
    "FAILED_FULFILMENT": r"recharge (nahi|nhi|fail)|recharge (hua|huwa) (hi )?nahi|balance (nahi|nhi) (aaya|aya)|"
                         r"bill (pay )?(nahi|fail)|recharge.*(nahi hua|fail)",
}
COMPATIBLE = {"CANCELLED": {"CANCELLED"}, "NOT_DELIVERED": {"NOT_DELIVERED", "CANCELLED"},
              "RETURNED": {"RETURNED"}, "FAILED_FULFILMENT": {"FAILED_FULFILMENT"}}
CONTRADICTING_CODES = {"FOREIGN_ORDER_CLAIMED", "FOREIGN_PAYMENT_CLAIMED", "ORDER_NOT_OWNED_BY_CUSTOMER",
                       "SCREENSHOT_LEDGER_MISMATCH", "CLAIM_CONTRADICTS_MERCHANT_RECORD"}
RRN_RE = re.compile(r"\b\d{12}\b")


def claimed_reason(text: str) -> str | None:
    t = (text or "").lower()
    for k in ("FAILED_FULFILMENT", "RETURNED", "NOT_DELIVERED", "CANCELLED"):
        if re.search(CLAIM_REASONS[k], t):
            return k
    return None


def rrns_in_text(text: str) -> list[str]:
    return RRN_RE.findall(text or "")


def inr(x: float) -> str:
    return f"₹{x:,.0f}"


def _when(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d %b")


class _Checks:
    def __init__(self):
        self.items: list[dict] = []
        self.codes: list[str] = []

    def add(self, cid: str, label: str, source: str, status: str, detail: str, code: str | None = None):
        self.items.append({"id": cid, "label": label, "source": source, "source_label": SOURCE_LABEL[source],
                           "trust": TRUST[source], "status": status, "detail": detail, "code": code})
        if code and status in ("FAIL", "WARN"):
            self.codes.append(code)


def _common(ck: _Checks, customer_id: str, row: dict, claim: ClaimedContext, history: dict) -> None:
    """Checks shared by every claim type: payment ownership, quoted payment refs, screenshot, claim history."""
    own = row["customer_id"] == customer_id
    ck.add("PAYMENT_FROM_CUSTOMER", "Payment exists and was made from this account", "BANK_LEDGER",
           "PASS" if own else "FAIL",
           f"{inr(row['verified_amount'])} to {row['counterparty']} on {_when(row['txn_timestamp'])} · RRN {row.get('rrn') or '—'}"
           if own else "This payment was not made from the customer's account", None if own else "FOREIGN_PAYMENT_CLAIMED")
    for rrn in [r for r in claim.refs if RRN_RE.fullmatch(r)]:
        hit = bank.find_by_rrn(rrn)
        if hit and hit["txn_id"] == row["txn_id"]:
            ck.add("QUOTED_PAYMENT_REF", f"Quoted payment reference {rrn}", "BANK_LEDGER", "PASS", "Matches this payment exactly")
        elif hit and hit["customer_id"] != customer_id:
            ck.add("QUOTED_PAYMENT_REF", f"Quoted payment reference {rrn}", "BANK_LEDGER", "FAIL",
                   "Belongs to a payment made from a different account", "FOREIGN_PAYMENT_CLAIMED")
        elif hit:
            ck.add("QUOTED_PAYMENT_REF", f"Quoted payment reference {rrn}", "BANK_LEDGER", "WARN",
                   f"Refers to a different payment of this customer ({hit['txn_id']})", "QUOTED_REF_OTHER_PAYMENT")
        else:
            ck.add("QUOTED_PAYMENT_REF", f"Quoted payment reference {rrn}", "BANK_LEDGER", "WARN",
                   "No payment with this reference exists (typo, or invented)", "UNKNOWN_PAYMENT_REF")
    if claim.ocr:
        bad = claim.ocr.amount is not None and abs(claim.ocr.amount - row["verified_amount"]) > 0.5
        ck.add("SCREENSHOT", "Screenshot agrees with the ledger", "SCREENSHOT", "FAIL" if bad else "PASS",
               f"Screenshot shows {inr(claim.ocr.amount)}, ledger shows {inr(row['verified_amount'])}" if bad
               else "Amount on the screenshot matches (screenshots are hints, never proof)",
               "SCREENSHOT_LEDGER_MISMATCH" if bad else None)
    prior = history.get("bank_disputes", 0) + history.get("contradicted_claims", 0)
    high = prior >= config.CLAIM_HISTORY_REVIEW_THRESHOLD
    ck.add("CLAIM_HISTORY", "Claim history", "CASE_HISTORY", "WARN" if high else "PASS",
           f"{history.get('bank_disputes', 0)} prior disputes on record, {history.get('contradicted_claims', 0)} claims "
           f"already found contradicted" + (f" (≥ {config.CLAIM_HISTORY_REVIEW_THRESHOLD}: a human signs off on any refund)"
                                            if high else ""), "CLAIM_HISTORY_REVIEW" if high else None)


def _risk(history: dict) -> dict:
    prior = history.get("bank_disputes", 0) + history.get("contradicted_claims", 0)
    return {"prior_claims": prior, "threshold": config.CLAIM_HISTORY_REVIEW_THRESHOLD,
            "review_required": prior >= config.CLAIM_HISTORY_REVIEW_THRESHOLD, **history}


# ------------------------------------------------------------------ payment failed (money left, payment didn't complete)
def evaluate_payment_failure(customer_id: str, row: dict, claim: ClaimedContext, cvf: dict, disc: dict,
                             history: dict) -> dict:
    ck = _Checks()
    _common(ck, customer_id, row, claim, history)
    failed = row["failure_stage"] != "NONE"
    ck.add("LEDGER_STATE", "Ledger confirms money is owed back", "BANK_LEDGER", "PASS" if failed else "FAIL",
           f"Gateway {row['gateway_status'].lower()}, bank {row['bank_status'].lower().replace('_', ' ')}" +
           ("" if failed else ": the beneficiary was credited, nothing is owed"),
           None if failed else "LEDGER_SHOWS_SUCCESS_NO_REFUND_DUE")
    if disc["discrepancy"]:
        ck.add("SYSTEMS_AGREE", "Gateway, bank and merchant agree", "BANK_LEDGER", "WARN",
               "Records disagree: " + ", ".join(f.replace("_", " ").lower() for f in disc["findings"]),
               "MULTI_SYSTEM_DISCREPANCY")
    for m in cvf["mismatches"]:
        if m["field"] == "status":
            ck.add("SCREENSHOT_STATUS", "Screenshot status agrees with the ledger", "SCREENSHOT", "FAIL",
                   f"Screenshot says {str(m['claimed']).lower()}, but the ledger says {str(m['ledger']).lower().replace('_', ' ')}",
                   "SCREENSHOT_LEDGER_MISMATCH")
        if m["field"] == "text_amount":
            ck.add("CLAIMED_AMOUNT", "Amount the customer quoted", "CUSTOMER_TEXT", "FAIL",
                   f"Said {inr(m['claimed'])}, ledger shows {inr(m['ledger'])}", "SCREENSHOT_LEDGER_MISMATCH")
    contradicted = any(c in CONTRADICTING_CODES for c in ck.codes)
    verdict = ("CONTRADICTED" if contradicted else "NOT_DUE" if not failed else
               "PARTIAL" if disc["discrepancy"] else "VERIFIED")
    return {"kind": "PAYMENT_FAILURE", "verdict": verdict, "codes": ck.codes, "checks": ck.items, "risk": _risk(history),
            "amount_paid": row["verified_amount"], "amount_due": row["verified_amount"] if failed else 0.0}


# ------------------------------------------------------------------ refund owed after a successful payment
def entitlement(order: MerchantOrder) -> tuple[float, str]:
    """(amount due, rule applied) from the category policy table. No category- or merchant-specific code."""
    rules = config.MERCHANT_REFUND_RULES[order.category]["due"]
    key = next((k for k in (f"{order.status}:{order.cancelled_by}", f"{order.status}:*") if k in rules), None)
    if not key:
        return 0.0, f"{order.status.replace('_', ' ').lower()} → no refund under {order.category.lower()} policy"
    due = order.amount if rules[key] == "full" else min(order.refundable_amount, order.amount)
    return due, f"{key} → {rules[key]}"


def evaluate_merchant(customer_id: str, row: dict, order: MerchantOrder | None, claim: ClaimedContext,
                      history: dict, other_claimants: list[str]) -> dict:
    ck = _Checks()
    _common(ck, customer_id, row, claim, history)
    reason = claimed_reason(claim.combined_text)
    text_amounts = extract_amounts(strip_refs(f"{claim.raw_text} {claim.voice_transcript}", claim.refs))   # typed/spoken only, not OCR noise
    base = {"kind": "MERCHANT_REFUND", "claimed_reason": reason, "amount_paid": row["verified_amount"],
            "risk": _risk(history)}

    if order is None:
        ck.add("MERCHANT_RECORD", "Merchant order record", "MERCHANT_RECORD", "WARN",
               f"No connector for {row['counterparty']}: we can't see the order, so we can't confirm a refund is due",
               "NO_MERCHANT_CONNECTOR")
        contradicted = any(c in CONTRADICTING_CODES for c in ck.codes)
        return {**base, "verdict": "CONTRADICTED" if contradicted else "UNVERIFIABLE", "codes": ck.codes,
                "checks": ck.items, "order": None, "amount_due": None, "amount_remaining": None}

    # 1. the order is really paid by *this* payment and belongs to *this* customer
    linked = order.payment_txn_id == row["txn_id"]
    owner = order.customer_ref == customer_id
    ck.add("ORDER_LINKED", f"{order.order_label} {order.order_ref} was paid by this payment", "MERCHANT_RECORD",
           "PASS" if linked and owner else "FAIL",
           f"{order.merchant_name}: {order.description}" if linked and owner else "Order is linked to a different payment or account",
           None if linked and owner else "ORDER_NOT_OWNED_BY_CUSTOMER")

    # 2. any reference the customer quoted must be this order
    for ref in [r for r in claim.refs if not RRN_RE.fullmatch(r)]:
        if ref == order.order_ref:
            continue
        other = order_by_ref(ref)
        if other and other.customer_ref != customer_id:
            ck.add("QUOTED_ORDER_REF", f"Quoted {other.order_label} {ref}", "MERCHANT_RECORD", "FAIL",
                   f"Exists, but was booked and paid from a different account ({other.status.lower().replace('_', ' ')})",
                   "FOREIGN_ORDER_CLAIMED")
        elif other:
            ck.add("QUOTED_ORDER_REF", f"Quoted {other.order_label} {ref}", "MERCHANT_RECORD", "WARN",
                   "Belongs to another of this customer's orders, not this payment", "QUOTED_REF_OTHER_ORDER")
        else:
            ck.add("QUOTED_ORDER_REF", f"Quoted reference {ref}", "MERCHANT_RECORD", "WARN",
                   "No merchant has an order with this reference (typo, or invented)", "UNKNOWN_ORDER_REF")

    # 3. what happened, according to the merchant (and does it match what the customer says?)
    status_txt = order.status.replace("_", " ").lower() + (f" by {order.cancelled_by.lower()}" if order.cancelled_by else "")
    if reason:
        ok = order.status in COMPATIBLE[reason]
        ck.add("STATUS_MATCHES_CLAIM", "What the customer says happened", "MERCHANT_RECORD", "PASS" if ok else "FAIL",
               f"Customer says {reason.replace('_', ' ').lower()}; merchant record says {status_txt} ({_when(order.status_at)})",
               None if ok else "CLAIM_CONTRADICTS_MERCHANT_RECORD")
    else:
        ck.add("STATUS_MATCHES_CLAIM", "Order status", "MERCHANT_RECORD", "PASS",
               f"Merchant record says {status_txt} ({_when(order.status_at)})")

    # 4. what the policy says is owed
    due, rule = entitlement(order)
    ck.add("ENTITLEMENT", "Refund due under the merchant's policy", "REFUND_POLICY", "PASS" if due > 0 else "FAIL",
           f"{inr(due)} due ({rule})" + (f"; {inr(order.amount - due)} kept as cancellation charges" if 0 < due < order.amount else "")
           if due > 0 else rule, None if due > 0 else "NO_REFUND_DUE_UNDER_POLICY")

    # 5. already paid back? in flight? double-dipping via chargeback?
    credited = sum(r.amount for r in order.refunds if r.status == "CREDITED")
    pending = sum(r.amount for r in order.refunds if r.status == "PENDING")
    remaining = max(0.0, due - credited)
    if credited:
        r0 = next(r for r in order.refunds if r.status == "CREDITED")
        ck.add("NOT_ALREADY_REFUNDED", "Not already refunded", "MERCHANT_RECORD", "FAIL" if remaining <= 0 else "WARN",
               f"{inr(credited)} already credited on {_when(r0.credited_at)} · UTR {r0.utr}",
               "ALREADY_REFUNDED" if remaining <= 0 else "PARTIALLY_REFUNDED")
    elif pending:
        ck.add("NOT_ALREADY_REFUNDED", "Not already refunded", "MERCHANT_RECORD", "WARN",
               f"Merchant already started a {inr(pending)} refund ({order.refunds[0].refund_ref}); we track it, never duplicate it",
               "MERCHANT_REFUND_IN_FLIGHT")
    elif due > 0:
        ck.add("NOT_ALREADY_REFUNDED", "Not already refunded", "MERCHANT_RECORD", "PASS", "No refund on record for this order")
    ck.add("NO_CHARGEBACK", "No chargeback raised for the same payment", "BANK_LEDGER",
           "FAIL" if order.chargeback_open else "PASS",
           "A chargeback is already open: refunding too would pay twice" if order.chargeback_open else "None open",
           "CHARGEBACK_OPEN" if order.chargeback_open else None)
    if other_claimants:
        # Informational for the real owner (someone else tried to claim their order); the other account's claim
        # already fails on ownership, so this never blocks the genuine customer.
        ck.add("CROSS_ACCOUNT", "Same order claimed from another account", "CASE_HISTORY", "WARN",
               f"{order.order_label} {order.order_ref} was also claimed from {len(other_claimants)} other account(s): "
               f"flagged to security. This customer owns the order, so their refund is unaffected.",
               "ORDER_ALSO_CLAIMED_ELSEWHERE")
    if text_amounts and due > 0 and max(text_amounts) > due + 0.5:
        ck.add("CLAIMED_AMOUNT", "Amount the customer asked for", "CUSTOMER_TEXT", "WARN",
               f"Asked for {inr(max(text_amounts))}; the record supports {inr(due)}. " +
               (f"Only {inr(remaining)} can be promised." if remaining > 0 else "Nothing more is owed."),
               "CLAIM_EXCEEDS_ENTITLEMENT")

    if any(c in CONTRADICTING_CODES for c in ck.codes):
        verdict = "CONTRADICTED"
    elif order.chargeback_open:
        verdict = "NEEDS_REVIEW"
    elif due <= 0:
        verdict = "NOT_DUE"
    elif remaining <= 0:
        verdict = "ALREADY_REFUNDED"
    elif pending >= remaining:
        verdict = "IN_FLIGHT"
    elif due < order.amount:
        verdict = "PARTIAL"
    else:
        verdict = "VERIFIED"
    rule = config.MERCHANT_REFUND_RULES[order.category]
    deadline = datetime.fromisoformat(order.status_at) + timedelta(days=rule["sla_days"])
    return {**base, "verdict": verdict, "codes": ck.codes, "checks": ck.items, "order": order.model_dump(),
            "amount_due": due, "amount_credited": credited, "amount_pending": pending, "amount_remaining": remaining,
            "deadline_iso": deadline.isoformat(), "sla_days": rule["sla_days"], "category_label": rule["label"]}
