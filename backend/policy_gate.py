"""Stage 3 - deterministic zero-trust policy gate. Pure Python; neither Nirnay nor CrewAI can bypass it.

The gate re-reads the amount from the trusted ledger by txn_id: callers cannot supply amounts.
"""
from datetime import datetime, timedelta

import clock
import ledger_db
import security
from config import (AGENT_WALLET_HOURLY_INR, AGENT_WALLET_PER_CASE_INR, BACKUP_APPROVER, PRIMARY_APPROVER,
                    RBI_CIRCULAR_REF, RBI_COMP_PER_DAY_INR, RBI_TAT_DAYS)
from mock_ledger import bank

KILL_SWITCH_ACTIVE = False

ROLE_CAPS = {
    "role:nirnay_triage": {"classify"},
    "role:crew_investigator": {"read_ledger"},
    "role:crew_compiler": {"draft_promise"},
    "role:crew_planner": {"plan"},
    "role:scoped_executor": {"execute_refund", "request_merchant_refund", "send_update"},
    "role:independent_auditor": {"read_ledger", "verify_proof"},
}
ALLOW, HOLD, BLOCK = "ALLOW_AUTONOMOUS", "REQUIRE_HUMAN_APPROVAL", "BLOCK"


def set_kill_switch(active: bool) -> None:
    global KILL_SWITCH_ACTIVE
    KILL_SWITCH_ACTIVE = bool(active)


# ------------------------------------------------------------------ RBI engine
def evaluate_rbi_tat_compliance(tx_type: str, failure_stage: str, txn_timestamp: datetime,
                                current_timestamp: datetime) -> dict:
    """Circular RBI/2019-20/67: P2P ceiling T+1, P2M ceiling T+5 (calendar days, IST);
    Rs.100/day compensation for each day beyond the ceiling, payable by the liable bank/PSP/acquirer."""
    if tx_type not in RBI_TAT_DAYS:
        raise ValueError(f"unsupported tx_type {tx_type}")
    ceiling = RBI_TAT_DAYS[tx_type]
    t_date = txn_timestamp.astimezone(clock.IST).date()
    c_date = current_timestamp.astimezone(clock.IST).date()
    deadline_date = t_date + timedelta(days=ceiling)
    deadline = datetime.combine(deadline_date, datetime.max.time().replace(microsecond=0), tzinfo=clock.IST)
    days_elapsed = (c_date - t_date).days
    delay_days = max(0, days_elapsed - ceiling)
    if tx_type == "P2P":
        category, liability = "P2P UPI transfer (beneficiary not credited / auto-reversal pending)", "Remitter Bank / PSP"
    else:
        category, liability = "P2M merchant UPI payment (debited, merchant confirmation failed / refund pending)", "Merchant Acquirer / Issuer Bank"
    return {
        "circular_ref": RBI_CIRCULAR_REF, "tx_category": category, "failure_stage": failure_stage,
        "tat_rule": f"T+{ceiling}", "tat_ceiling_days": ceiling, "tat_deadline_iso": deadline.isoformat(),
        "days_elapsed": days_elapsed, "days_until_breach": ceiling - days_elapsed, "delay_days": delay_days,
        "is_tat_breached": days_elapsed > ceiling,
        "accrued_compensation_inr": delay_days * RBI_COMP_PER_DAY_INR,
        "comp_rate_inr_per_day": RBI_COMP_PER_DAY_INR, "liability_entity": liability,
        "vaada_pays_compensation": False,
        "disclaimer": "Compensation is a statutory liability of the entity named in liability_entity; "
                      "VAADA only tracks the exposure and never disburses it."}


# ------------------------------------------------------------------ gate
def _decision(decision, codes, **kw):
    return {"decision": decision, "reason_codes": codes, **kw}


def evaluate_action(txn_id: str, action_type: str, role_token: str, *, nirnay_route: str | None = None,
                    nirnay_action: str | None = None, adversarial: bool = False,
                    claim_ledger_mismatch: bool = False, human_approved: bool = False, risk_review: bool = False) -> dict:
    """Non-mutating policy evaluation for a refund-class action."""
    role = security.verify_capability(role_token)
    if role is None or action_type not in ROLE_CAPS.get(role, set()):
        return _decision(BLOCK, ["ROLE_NOT_AUTHORIZED"], role=role)
    row = bank.get(txn_id)
    if not row:
        return _decision(BLOCK, ["TXN_NOT_IN_LEDGER"])
    amount = row["verified_amount"]            # trusted amount, never caller supplied
    codes: list[str] = []
    if row["failure_stage"] == "NONE":
        codes.append("LEDGER_SHOWS_SUCCESS_NO_REFUND_DUE")
    if adversarial:
        codes.append("ADVERSARIAL_INPUT_CONTAINED")
    if claim_ledger_mismatch:
        codes.append("SCREENSHOT_LEDGER_MISMATCH")
    if codes:
        return _decision(BLOCK, codes, amount=amount)
    if nirnay_action == "blocked":
        return _decision(BLOCK, ["NIRNAY_ROUTED_BLOCKED"], amount=amount)
    if ledger_db.idem_exists(txn_id, action_type):
        return _decision(BLOCK, ["DUPLICATE_ACTION_BLOCKED"], amount=amount,
                         idempotency_key=ledger_db.idem_key(txn_id, action_type))
    if human_approved:
        return _decision(ALLOW, ["HUMAN_APPROVED"], amount=amount)
    if KILL_SWITCH_ACTIVE:
        codes.append("KILL_SWITCH_ACTIVE")
    if risk_review:
        codes.append("CLAIM_HISTORY_REVIEW")
    if amount > AGENT_WALLET_PER_CASE_INR:
        codes.append("POLICY_HOLD: EXCEEDS_AGENT_WALLET_CAP")
    if ledger_db.wallet_spent_last_hour() + amount > AGENT_WALLET_HOURLY_INR:
        codes.append("HOURLY_AGENT_BUDGET_EXHAUSTED")
    if nirnay_action in ("human_approval", "human_investigation"):
        codes.append(f"NIRNAY_ROUTED_{nirnay_action.upper()}")
    if nirnay_route == "slow_path_crewai" and not codes:
        codes.append("SLOW_PATH_REQUIRES_HUMAN_SIGNOFF")
    if codes:
        return _decision(HOLD, codes, amount=amount)
    return _decision(ALLOW, ["WITHIN_AGENT_WALLET"], amount=amount)


REFUNDABLE_VERDICTS = {"VERIFIED", "PARTIAL"}


def evaluate_merchant_refund(txn_id: str, role_token: str, evidence: dict, *, adversarial: bool = False,
                             human_approved: bool = False) -> dict:
    """Gate for asking a merchant to refund an order. The amount is re-derived from the merchant's own record by the
    verifier (never from the customer); the merchant funds it, so the agent wallet is not touched."""
    action = "request_merchant_refund"
    role = security.verify_capability(role_token)
    if role is None or action not in ROLE_CAPS.get(role, set()):
        return _decision(BLOCK, ["ROLE_NOT_AUTHORIZED"], role=role)
    amount = evidence.get("amount_remaining") or 0.0
    codes = []
    if adversarial:
        codes.append("ADVERSARIAL_INPUT_CONTAINED")
    if evidence["verdict"] not in REFUNDABLE_VERDICTS:
        codes.append(f"CLAIM_{evidence['verdict']}")
    if amount <= 0:
        codes.append("NOTHING_LEFT_TO_REFUND")
    if codes:
        return _decision(BLOCK, codes, amount=amount)
    if ledger_db.idem_exists(txn_id, action):
        return _decision(BLOCK, ["DUPLICATE_ACTION_BLOCKED"], amount=amount, idempotency_key=ledger_db.idem_key(txn_id, action))
    if human_approved:
        return _decision(ALLOW, ["HUMAN_APPROVED"], amount=amount)
    if KILL_SWITCH_ACTIVE:
        codes.append("KILL_SWITCH_ACTIVE")
    if evidence["risk"]["review_required"]:
        codes.append("CLAIM_HISTORY_REVIEW")
    if codes:
        return _decision(HOLD, codes, amount=amount)
    return _decision(ALLOW, ["MERCHANT_RECORD_CONFIRMS_REFUND"], amount=amount)


def execute_merchant_refund(txn_id: str, order_ref: str, role_token: str, evidence: dict, *,
                            human_approval: dict | None = None, adversarial: bool = False) -> dict:
    import merchant_rails
    approved = False
    if human_approval:
        ok = security.verify_approval(human_approval["payload"], human_approval["token"])
        exp = datetime.fromisoformat(human_approval["payload"]["expiry"])
        if not ok or exp < clock.now() or human_approval["payload"]["txn_id"] != txn_id:
            return {**_decision(BLOCK, ["INVALID_OR_EXPIRED_APPROVAL_TOKEN"]), "executed": False}
        approved = True
    verdict = evaluate_merchant_refund(txn_id, role_token, evidence, adversarial=adversarial, human_approved=approved)
    if verdict["decision"] != ALLOW:
        return {**verdict, "executed": False}
    key = ledger_db.idem_key(txn_id, "request_merchant_refund")
    if not ledger_db.idem_acquire(txn_id, "request_merchant_refund"):
        return {**_decision(BLOCK, ["DUPLICATE_ACTION_BLOCKED"], idempotency_key=key), "executed": False}
    api = merchant_rails.request_refund(order_ref, verdict["amount"], key)
    return {**verdict, "executed": True, "idempotency_key": key, "refund_api": api}


def issue_approval_request(txn_id: str, action_type: str, amount: float, assigned_to: str, ttl_hours: float = 240) -> dict:
    expiry = (clock.now() + timedelta(hours=ttl_hours)).isoformat()
    payload = {"txn_id": txn_id, "action": action_type, "amount": amount, "expiry": expiry,
               "nonce": security.sha256_hex(txn_id + action_type + clock.now().isoformat())[:16]}
    return {"payload": payload, "token": security.sign_approval(payload), "algo": "HMAC-SHA256", "assigned_to": assigned_to}


def execute_action(txn_id: str, action_type: str, role_token: str, *, human_approval: dict | None = None,
                   **ctx) -> dict:
    """Run the refund through the gate; acquires the idempotency lock atomically and calls the refund API."""
    approved = False
    if human_approval:
        ok = security.verify_approval(human_approval["payload"], human_approval["token"])
        exp = datetime.fromisoformat(human_approval["payload"]["expiry"])
        if not ok or exp < clock.now() or human_approval["payload"]["txn_id"] != txn_id:
            return _decision(BLOCK, ["INVALID_OR_EXPIRED_APPROVAL_TOKEN"])
        approved = True
    verdict = evaluate_action(txn_id, action_type, role_token, human_approved=approved, **ctx)
    if verdict["decision"] != ALLOW:
        return {**verdict, "executed": False}
    key = ledger_db.idem_key(txn_id, action_type)
    if not ledger_db.idem_acquire(txn_id, action_type):       # race-safe second line of defence
        return {**_decision(BLOCK, ["DUPLICATE_ACTION_BLOCKED"], idempotency_key=key), "executed": False}
    amount = verdict["amount"]
    api = bank.initiate_refund(txn_id, amount, key)
    if not approved:
        ledger_db.wallet_record(txn_id, amount)
    return {**verdict, "executed": True, "idempotency_key": key, "refund_api": api}
