"""Case pipeline: Nirnay -> CrewAI (on abstain) -> Policy Gate -> Breach Radar -> Proof-of-Done verifier.

Also owns customer notifications, so every state change the customer should hear about lands in their chat.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta

import breach_radar
import claim_verifier
import clock
import config
import data_and_nirnay as dn
import ledger_db
import merchant_rails
import mock_ledger
import policy_gate
import sarvam_adapter
import scenarios
import security
import vaada_crew
import verifier
from mock_ledger import bank
from untrusted_ingestion import (ClaimedContext, OcrClaim, claim_vs_fact, multi_system_discrepancy,
                                 scan_for_injection, verified_from_row)

LOCK = threading.RLock()
STATE: dict = {"model": None, "bench": None}
EXEC_TOKEN = security.issue_capability("role:scoped_executor")
AUDIT_TOKEN = security.issue_capability("role:independent_auditor")


def inr(x: float) -> str:
    return f"₹{x:,.0f}"


def fmt_when(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d %b, %I:%M %p")


# ------------------------------------------------------------------ conversations / notifications
def blank_conversation(customer_id: str) -> dict:
    name = scenarios.BY_ID[customer_id]["name"].split()[0]
    return {"customer_id": customer_id, "pending": None, "messages": [{
        "id": uuid.uuid4().hex, "from": "vaada", "at": clock.now().isoformat(),
        "text": f"Namaste {name}! Main VAADA hoon. UPI payment ya refund me koi problem ho to apne shabdon me batayein — "
                f"likh kar, bol kar, ya screenshot bhej kar."}]}


def load_conversation(customer_id: str) -> dict:
    return ledger_db.load_conversation(customer_id) or blank_conversation(customer_id)


def notify(customer_id: str, text: str, **kw) -> None:
    conv = load_conversation(customer_id)
    conv["messages"].append({"id": uuid.uuid4().hex, "from": "vaada", "text": text, "at": clock.now().isoformat(), **kw})
    ledger_db.save_conversation(conv)


def _notify_case(case: dict, text: str, kind: str) -> None:
    notify(case["customer"]["id"], text, kind=kind, case_id=case["txn_id"])


# ------------------------------------------------------------------ chain helpers
def _event(case: dict, name: str, data: dict) -> None:
    e = ledger_db.append_chain(case["txn_id"], name, case["state"], data)
    case.setdefault("events", []).append({"event": name, "at": e.timestamp, "data": data})


def _transition(case: dict, to_state: str, name: str, data: dict) -> None:
    e = ledger_db.append_chain(case["txn_id"], name, to_state, data, from_state=case["state"])
    case["state"] = to_state
    case.setdefault("events", []).append({"event": name, "at": e.timestamp, "data": data, "to_state": to_state})


def _refresh(case: dict) -> dict:
    return breach_radar.refresh(case, _event, _notify_case)


# ------------------------------------------------------------------ customer-facing copy
def contradiction_text(case: dict) -> str:
    """Customer copy when their claim (screenshot / quoted amount / quoted payment) contradicts the bank ledger."""
    m = case["claim_vs_fact"]["mismatches"][0]
    amt = inr(case["verified"]["verified_amount"])
    who = case["counterparty"].split(" (")[0]
    if m["field"] == "payment_ref":
        return ("Aapne jo payment reference bataya wo kisi doosre account ke payment ka hai, aapke account ka nahi. "
                "Isliye is par refund process nahi ho sakta. Case specialist review me bheja gaya hai.")
    if m["field"] == "status":
        return (f"Aapke screenshot me payment 'failed' dikh raha hai, lekin bank records ke hisaab se {amt} ka payment {who} ko "
                f"successfully credit ho chuka hai. Isliye refund process nahi ho sakta. Case specialist review me bheja gaya hai.")
    return (f"Aapne jo amount bataya (₹{float(m['claimed']):,.0f}) wo bank records ({inr(m['ledger'])}) se match nahi karta. "
            f"Case specialist review me bheja gaya hai; 24 ghante me update denge.")


def status_text(case: dict) -> str:
    if case.get("kind") == "MERCHANT_REFUND":
        return merchant_text(case)
    if case.get("outcome") == "CLAIM_CONTRADICTED":
        return contradiction_text(case)
    amt = inr(case["verified"]["verified_amount"])
    p = case.get("promise")
    when = fmt_when(p["draft"]["by_when"]) if p else None
    out = case.get("outcome")
    if out == "CONTAINED":
        return "Ye request security review me hai. Ledger se verify hue bina koi payout nahi hoga."
    if out == "NO_REFUND_DUE":
        return f"Hamare records ke hisaab se {amt} ka payment successfully credit ho chuka hai, isliye refund due nahi hai."
    if out == "REJECTED":
        return "Aapka case manual review me hai. Ek specialist 24 ghante me aapse sampark karega."
    if (case.get("crew") or {}).get("mode") == "RUNNING":
        return f"Hamari specialist team aapka {amt} wala case abhi check kar rahi hai. Kuch hi minute me update dunga."
    if case["state"] == "VERIFIED_CLOSED":
        utr = (case.get("verification") or {}).get("receipt", {}).get("utr", "")
        return f"Ye case close ho chuka hai: {amt} aapke account me credit ho gaya (UTR {utr})."
    if case["state"] == "AWAITING_PROOF":
        return (f"{amt} ka refund bank ko bhej diya gaya hai. Bank se settlement (UTR) confirm hote hi main aapko bataunga. "
                f"Humara vaada: {when} tak.")
    if case["state"] == "AWAITING_APPROVAL":
        return f"{amt} ka refund finance team ke approval me hai. Agla update {when} tak pakka."
    return f"Aapka {amt} wala case review me hai. Agla update {when or '24 ghante'} tak."


def _ack(case: dict) -> str:
    if case.get("kind") == "MERCHANT_REFUND":
        return merchant_text(case, ack=True)
    amt = inr(case["verified"]["verified_amount"])
    who = case["counterparty"].split(" (")[0]
    out, gate = case.get("outcome"), case.get("gate") or {}
    codes = gate.get("reason_codes", [])
    if out == "CONTAINED":
        return ("Aapka message hamari security team ko bhej diya gaya hai. Is account par koi automatic refund nahi hoga "
                "jab tak details bank records se match na hon.")
    if out == "NO_REFUND_DUE":
        return (f"Maine check kiya: {amt} ka payment {who} ko {fmt_when(case['verified']['txn_timestamp'])} "
                f"par successfully credit ho chuka hai. Isliye refund due nahi hai. Kuch aur madad chahiye to batayein.")
    if out == "CLAIM_CONTRADICTED" or "SCREENSHOT_LEDGER_MISMATCH" in codes:
        return contradiction_text(case)
    p = case["promise"]["draft"]
    if case["state"] == "AWAITING_PROOF":
        return (f"Maine aapka {amt} ka payment ({who} ko) check kar liya — reversal pending tha. "
                f"Refund initiate kar diya hai. Humara vaada: paisa {fmt_when(p['by_when'])} tak aapke account me. "
                f"Bank se UTR confirm hone tak case open rahega.")
    if case["state"] == "AWAITING_APPROVAL":
        why = case["crew"]["diagnosis"]["root_cause"] if case.get("crew") else "Amount auto-refund limit se zyada hai."
        promise = p.get("narrower_controllable_commitment") and \
            f"Settlement bank ke haath me hai, par main har kadam track karunga aur {fmt_when(p['by_when'])} tak update dunga."
        return (f"Maine aapka {amt} ka case ({who}) verify kar liya. {_hinglish_root(case)} "
                f"Refund finance team ke approval ke liye bheja gaya hai. "
                f"{promise or 'Agla update ' + fmt_when(p['by_when']) + ' tak.'}")
    return status_text(case)


def _hinglish_root(case: dict) -> str:
    f = (case.get("discrepancy") or {}).get("findings", [])
    if "GATEWAY_FAILED_BUT_BANK_DEBITED" in f:
        return "Bank ne paise kaate, lekin payment merchant tak nahi pahuncha aur auto-reversal nahi hua."
    if case["verified"]["failure_stage"] == "DUPLICATE_DEBIT":
        return "Ek hi payment do baar debit hua hai."
    return ""


STATUS_HI = {"CANCELLED": "cancel ho chuka hai", "NOT_DELIVERED": "deliver nahi hua", "FAILED_FULFILMENT": "fail hua",
             "RETURNED": "return mil chuka hai", "FULFILLED": "poora ho chuka hai", "PENDING": "abhi process me hai"}
BY_HI = {"MERCHANT": "merchant ki taraf se", "CUSTOMER": "aapki taraf se", "SYSTEM": "system ki taraf se"}


def merchant_text(case: dict, ack: bool = False) -> str:
    """Customer-facing copy for refunds owed after a successful payment. Every fact quoted comes from the records."""
    ev = case["evidence"]
    o = ev.get("order") or {}
    amt = inr(case["verified"]["verified_amount"])
    who = o.get("merchant_name") or case["counterparty"].split(" (")[0]
    ref = f"{o.get('order_label')} {o.get('order_ref')}" if o else ""
    st = f"{STATUS_HI.get(o.get('status'), '')}" + (f" ({BY_HI[o['cancelled_by']]})" if o.get("cancelled_by") else "")
    out, p = case.get("outcome"), (case.get("promise") or {}).get("draft")
    fail = next((c for c in ev["checks"] if c["status"] == "FAIL" and c["code"] in claim_verifier.CONTRADICTING_CODES), None)
    if out == "CONTAINED":
        return "Ye request security review me hai. Records se verify hue bina koi payout nahi hoga."
    if out == "CLAIM_CONTRADICTED":
        if fail and fail["code"] == "FOREIGN_ORDER_CLAIMED":
            return (f"Aapne jo {fail['label'].replace('Quoted ', '')} bataya, wo booking aapke account se pay nahi hui hai. "
                    f"Isliye is par refund process nahi ho sakta. Agar booking aapki hai, to jis account se payment hua tha "
                    f"wahan se request karein.")
        if fail and fail["code"] == "CLAIM_CONTRADICTS_MERCHANT_RECORD":
            return (f"{who} ke record ke hisaab se aapka {ref} {st}. Isliye refund due nahi dikh raha. "
                    f"Agar aapke paas aur jaankari hai to bhejiye; case specialist review me hai.")
        return "Aapki di hui details hamare bank aur merchant records se match nahi karti. Case specialist review me hai."
    if out == "DISPUTE_RAISED":
        d = case["dispute"]
        return (f"Maine verify kiya: {amt} ka payment {who} ko hua tha. Is merchant ke order records hamare paas direct nahi "
                f"hain, isliye main refund ka vaada nahi kar sakta. Maine merchant ke saath dispute {d['ref']} raise kiya hai; "
                f"unka jawab {fmt_when(d['respond_by'])} tak aapko bataunga.")
    if out == "NO_REFUND_DUE":
        return f"{who} ke record ke hisaab se aapka {ref} {st}, isliye policy ke under refund due nahi hai."
    if out == "ALREADY_REFUNDED":
        r = next(r for r in o["refunds"] if r["status"] == "CREDITED")
        charge = o["amount"] - ev["amount_due"]
        return (f"Aapka {ref} {st}. Policy ke hisaab se {inr(ev['amount_due'])} refund banta tha"
                + (f" ({inr(charge)} cancellation charge)" if charge > 0 else "")
                + f", jo {fmt_when(r['credited_at'])} ko aapke account me credit ho chuka hai (UTR {r['utr']}). "
                  f"Isliye aur refund due nahi hai.")
    if out in ("MANUAL_REVIEW", "REJECTED"):
        extra = " Is payment par aapke bank me pehle se chargeback chal raha hai, do jagah se refund nahi ho sakta." \
            if "CHARGEBACK_OPEN" in ev["codes"] else ""
        return f"Aapka case manual review me hai.{extra} Ek specialist 24 ghante me update dega."
    due = inr(case.get("expected_refund") or ev.get("amount_remaining") or 0)
    by = fmt_when(p["by_when"]) if p else "24 ghante"
    partial = f" (cancellation charges ke baad)" if ev["verdict"] == "PARTIAL" else ""
    if case["state"] == "VERIFIED_CLOSED":
        utr = (case.get("verification") or {}).get("receipt", {}).get("utr", "")
        return f"Ye case close ho chuka hai: {due} aapke account me credit ho gaya (UTR {utr})."
    if case["state"] == "AWAITING_PROOF":
        if ev["verdict"] == "IN_FLIGHT":
            return (f"Maine check kiya: {who} ke record me aapka {ref} {st}, aur unhone {due} ka refund pehle hi shuru kar "
                    f"diya hai. Main ise track kar raha hoon. Humara vaada: paisa {by} tak aapke account me. "
                    f"Credit confirm hone tak case open rahega.")
        lead = (f"Maine verify kar liya: {who} ke record me aapka {ref} {st}, aur {due} refund banta hai{partial}. " if ack
                else f"{due} ka refund {who} se request ho chuka hai. ")
        return lead + f"Refund request merchant ko bhej di hai. Humara vaada: paisa {by} tak aapke account me. Credit confirm hone tak case open rahega."
    if case["state"] == "AWAITING_APPROVAL":
        return (f"Maine verify kar liya: aapka {ref} {st}, {due} refund banta hai{partial}. Refund request finance team ke "
                f"review ke baad merchant ko jayegi. Agla update {by} tak pakka.")
    return f"Aapka {amt} wala case review me hai. Agla update {by} tak."


def triage_summary(case: dict) -> dict:
    if case.get("kind") == "MERCHANT_REFUND" and case.get("outcome") != "CONTAINED":
        ev = case["evidence"]
        fail = next((c for c in ev["checks"] if c["status"] == "FAIL"), None)
        return {
            "VERIFIED": {"label": "Verified by records", "tone": "green",
                         "text": "Payment, order ownership and status confirmed against bank and merchant systems."},
            "PARTIAL": {"label": "Partly refundable", "tone": "green",
                        "text": "Records confirm the claim; the merchant's policy refunds part of the payment."},
            "IN_FLIGHT": {"label": "Merchant already refunding", "tone": "green",
                          "text": "Records confirm the claim and the merchant has already started the refund; VAADA tracks it."},
            "CONTRADICTED": {"label": "Claim contradicted", "tone": "red",
                             "text": fail["detail"] if fail else "Claim conflicts with the records."},
            "UNVERIFIABLE": {"label": "Can't verify yet", "tone": "amber",
                             "text": "No merchant connector: payment confirmed, refund entitlement unknown. Dispute raised, no money promised."},
            "NOT_DUE": {"label": "No refund due", "tone": "slate", "text": fail["detail"] if fail else "Merchant record shows nothing owed."},
            "ALREADY_REFUNDED": {"label": "Already refunded", "tone": "slate",
                                 "text": "The merchant already credited everything that was owed."},
            "NEEDS_REVIEW": {"label": "Manual review", "tone": "amber", "text": "A chargeback is open on the same payment."},
        }[ev["verdict"]]
    n = case["nirnay"]
    if case.get("outcome") == "CLAIM_CONTRADICTED":
        m = case["claim_vs_fact"]["mismatches"][0]
        return {"label": "Claim contradicted", "tone": "red",
                "text": "The screenshot or quoted details conflict with the bank ledger. No LLM call, no promise made." if m["field"] != "payment_ref"
                else "The quoted payment belongs to a different account. No LLM call, no promise made."}
    if case.get("outcome") == "CONTAINED":
        return {"label": "Contained", "tone": "red", "text": "Prompt-injection attempt detected in customer input. Never reached an LLM."}
    if case.get("outcome") == "NO_REFUND_DUE":
        return {"label": "No refund due", "tone": "slate", "text": "Ledger shows the payment was credited to the beneficiary."}
    if n["overall_route"] == "fast_path":
        return {"label": "Auto-triaged", "tone": "green", "text": "Nirnay was confident above its learned threshold. Resolved locally with no LLM call."}
    reasons = {"MULTI_SYSTEM_DISCREPANCY": "gateway, bank and merchant records disagree",
               "CLAIM_LEDGER_MISMATCH": "customer's claim contradicts the ledger",
               "ISSUE_CONFIDENCE_BELOW_TAU_STAR": "issue type was uncertain",
               "ACTION_CONFIDENCE_BELOW_TAU_STAR": "the right action was uncertain"}
    why = ", ".join(reasons[r] for r in n["reason_codes"] if r in reasons)
    return {"label": "Specialist review", "tone": "amber", "text": f"Escalated to the specialist squad because {why}."}


# ------------------------------------------------------------------ evidence helpers
def _history(customer_id: str, row: dict, exclude: str | None = None) -> dict:
    contradicted = 0
    for t in ledger_db.list_case_ids():
        c = ledger_db.load_case(t)
        if c and t != exclude and c["customer"]["id"] == customer_id and (c.get("evidence") or {}).get("verdict") == "CONTRADICTED":
            contradicted += 1
    return {"bank_disputes": row["dispute_history_count"], "contradicted_claims": contradicted}


def _other_claimants(refs: list[str], customer_id: str) -> list[str]:
    """Other accounts whose cases point at the same order (as their own order or as a quoted reference)."""
    out = set()
    for t in ledger_db.list_case_ids():
        c = ledger_db.load_case(t)
        if not c or c["customer"]["id"] == customer_id:
            continue
        theirs = set(c["claimed"].get("refs") or [])
        o = (c.get("evidence") or {}).get("order")
        if o:
            theirs.add(o["order_ref"])
        if theirs & set(refs):
            out.add(c["customer"]["id"])
    return sorted(out)


def _merchant_evidence(case: dict) -> dict:
    """Re-read the bank and merchant systems and re-verify. Always fresh: never trusts an earlier verdict."""
    row = bank.get(case["txn_id"])
    order = merchant_rails.order_for_payment(case["txn_id"])
    claim = ClaimedContext(**{k: v for k, v in case["claimed"].items() if k != "untrusted"})
    return claim_verifier.evaluate_merchant(case["customer"]["id"], row, order, claim,
                                            _history(case["customer"]["id"], row, exclude=case["txn_id"]),
                                            _other_claimants([order.order_ref], case["customer"]["id"]) if order else [])


def _is_merchant_claim(v, claim: ClaimedContext, txn_id: str) -> bool:
    """Payment succeeded at a merchant, and either we can see the order or the customer says something went wrong
    after paying (cancelled / not delivered / returned / recharge failed) or quotes an order reference."""
    if v.failure_stage != "NONE" or v.tx_type != "P2M":
        return False
    return (merchant_rails.order_for_payment(txn_id) is not None or claim_verifier.claimed_reason(claim.combined_text) is not None
            or any(not claim_verifier.RRN_RE.fullmatch(r) for r in claim.refs))


# ------------------------------------------------------------------ pipeline
def open_case(customer_id: str, txn_id: str, text: str, *, voice_transcript: str = "", voice_mode: str | None = None,
              ocr: dict | None = None, attachment: dict | None = None, refs: list[str] | None = None) -> dict:
    model: dn.Nirnay = STATE["model"]
    row = bank.get(txn_id)
    v = verified_from_row(row)
    now = clock.now()

    claim = ClaimedContext(raw_text=text, voice_transcript=voice_transcript, voice_mode=voice_mode,
                           ocr=OcrClaim(**ocr) if ocr else None, refs=refs or [])
    scan = scan_for_injection(claim)
    cvf = claim_vs_fact(claim, v)
    disc = multi_system_discrepancy(v)
    merchant_claim = not scan["adversarial"] and _is_merchant_claim(v, claim, txn_id)
    evidence = None
    if not merchant_claim:
        evidence = claim_verifier.evaluate_payment_failure(customer_id, row, claim, cvf, disc,
                                                           _history(customer_id, row, exclude=txn_id))
        if "FOREIGN_PAYMENT_CLAIMED" in evidence["codes"] and not cvf["claim_ledger_mismatch"]:
            cvf["mismatches"].append({"field": "payment_ref", "claimed": "another account's payment", "ledger": txn_id})
            cvf.update(claim_ledger_mismatch=True, code="SCREENSHOT_LEDGER_MISMATCH")
    hours = (now - datetime.fromisoformat(v.txn_timestamp)).total_seconds() / 3600
    feats = {"verified_amount": v.verified_amount, "hours_elapsed": hours, "tx_type": v.tx_type,
             "gateway_status": v.gateway_status, "bank_webhook_received": v.bank_webhook_received,
             "claim_ledger_mismatch": cvf["claim_ledger_mismatch"], "user_dispute_history_count": v.user_dispute_history_count}
    decision = model.predict(claim.combined_text, feats, multi_system_discrepancy=disc["discrepancy"])
    rbi = policy_gate.evaluate_rbi_tat_compliance(v.tx_type, v.failure_stage, datetime.fromisoformat(v.txn_timestamp), now)

    case = {"txn_id": txn_id, "created_at": now.isoformat(), "state": "OPEN_TRIAGED", "outcome": None,
            "customer": {"id": customer_id, "name": scenarios.BY_ID[customer_id]["name"]},
            "counterparty": row.get("counterparty") or "", "claimed": claim.model_dump(), "attachments": [attachment] if attachment else [],
            "verified": v.model_dump(), "features": feats, "injection": scan, "claim_vs_fact": cvf, "discrepancy": disc,
            "nirnay": decision, "crew": None, "promise": None, "gate": None, "approval": None, "execution": None,
            "kind": "MERCHANT_REFUND" if merchant_claim else "PAYMENT_FAILURE", "evidence": evidence,
            "events": [], "identity_trail": [{"role": "role:nirnay_triage", "capability": "classify", "ok": True}]}
    ledger_db.append_chain(txn_id, "OPEN_TRIAGED", "OPEN_TRIAGED", {
        "customer_text": claim.combined_text[:280], "nirnay_route": decision["overall_route"],
        "reason_codes": decision["reason_codes"], "issue": decision["issue_pred"],
        "issue_conf": round(decision["issue_confidence"], 4), "latency_ms": round(decision["latency_ms"], 3)}, from_state="NONE")

    if scan["adversarial"]:
        case["outcome"] = "CONTAINED"           # contained BEFORE any LLM sees the text
        case["gate"] = policy_gate.evaluate_action(txn_id, "execute_refund", EXEC_TOKEN, adversarial=True,
                                                   claim_ledger_mismatch=cvf["claim_ledger_mismatch"])
        _event(case, "ADVERSARIAL_INPUT_CONTAINED", {"patterns": scan["patterns"], "gate": case["gate"]["reason_codes"]})
    elif merchant_claim:
        case["evidence"] = ev = _merchant_evidence(case)
        _event(case, "CLAIM_VERIFIED", {"verdict": ev["verdict"], "codes": ev["codes"], "amount_due": ev.get("amount_due"),
                                        "sources": sorted({c["source"] for c in ev["checks"]})})
        _merchant_decide(case)
    elif cvf["claim_ledger_mismatch"]:
        # The customer's evidence contradicts the ledger: settle it locally, no LLM and no promise.
        case["outcome"] = "CLAIM_CONTRADICTED"
        case["gate"] = policy_gate.evaluate_action(txn_id, "execute_refund", EXEC_TOKEN, claim_ledger_mismatch=True)
        _event(case, "POLICY_BLOCK", {"reason_codes": case["gate"]["reason_codes"]})
    elif v.failure_stage == "NONE" and not cvf["claim_ledger_mismatch"]:
        case["outcome"] = "NO_REFUND_DUE"
        case["gate"] = policy_gate.evaluate_action(txn_id, "execute_refund", EXEC_TOKEN)
        _event(case, "LEDGER_SHOWS_SUCCESS", {"bank_status": v.bank_status, "gate": case["gate"]["reason_codes"]})
    elif decision["abstain"] and sarvam_adapter.has_key():
        # Live specialist squad is slow (LLM calls): acknowledge now, finish in the background.
        case["crew"] = {"mode": "RUNNING", "started_at": now.isoformat(), "timeout_s": config.CREW_TIMEOUT_S}
        _event(case, "SPECIALISTS_ENGAGED", {"mode": "CREWAI_SARVAM_LIVE", "timeout_s": config.CREW_TIMEOUT_S})
        case["triage"] = triage_summary(case)
        _refresh(case)
        ledger_db.save_case(case)
        _notify_case(case, f"Maine aapka {inr(v.verified_amount)} ka case dekh liya. Bank aur merchant ke records me farak hai, "
                           f"isliye hamari specialist team ise check kar rahi hai. Kuch hi minute me pakka update dunga.", "ack")
        threading.Thread(target=_finish_slow_path, args=(txn_id,), daemon=True).start()
        return case
    else:
        crew = vaada_crew.run_slow_path(claim, v, decision, rbi, now, allow_live=False) if decision["abstain"] else None
        _promise_and_gate(case, crew)

    case["triage"] = triage_summary(case)
    _refresh(case)
    ledger_db.save_case(case)
    _notify_case(case, _ack(case), "ack")
    return case


def _promise_and_gate(case: dict, crew: dict | None) -> None:
    """Compile the promise (from the crew draft or deterministically), score it with Head 3, then run the gate."""
    v = verified_from_row(bank.get(case["txn_id"]))
    now = clock.now()
    rbi = policy_gate.evaluate_rbi_tat_compliance(v.tx_type, v.failure_stage, datetime.fromisoformat(v.txn_timestamp), now)
    if crew:
        case["crew"] = crew
    if case["claim_vs_fact"]["claim_ledger_mismatch"]:
        # A claim that contradicts the ledger earns no promise to pay: block, record, hand to manual review.
        case["gate"] = policy_gate.evaluate_action(case["txn_id"], "execute_refund", EXEC_TOKEN,
                                                   claim_ledger_mismatch=True)
        _event(case, "POLICY_BLOCK", {"reason_codes": case["gate"]["reason_codes"]})
        return
    if crew:
        caps = {"role:crew_investigator": "read_ledger", "role:crew_compiler": "draft_promise", "role:crew_planner": "plan"}
        case["identity_trail"] += [{"role": a["role"], "capability": caps[a["role"]], "ok": True} for a in crew["agents"]]
        draft = crew["draft"]
    else:
        diag = vaada_crew.CaseDiagnosis(root_cause="Confident local classification; ledger consistent.",
                                        issue_type=case["nirnay"]["issue_pred"])
        draft = vaada_crew._compile(diag, v, rbi, now).model_dump()
    cvf = case["claim_vs_fact"]
    head3 = dn.assess_promise_policy(draft, {"amount": v.verified_amount, "claim_ledger_mismatch": cvf["claim_ledger_mismatch"],
                                             "bank_webhook_received": v.bank_webhook_received, "rbi": rbi,
                                             "route": case["nirnay"]["overall_route"], "now_iso": now.isoformat()})
    case["promise"] = {"draft": draft, "head3": head3, "created_at": now.isoformat()}
    _transition(case, "PROMISE_ACTIVE", "PROMISE_COMPILED", {"what": draft["what"], "by_when": draft["by_when"],
                "proof_check_id": draft["proof_check_id"], "head3": head3})
    run_gate(case)


def _merchant_decide(case: dict) -> None:
    ev, now = case["evidence"], clock.now()
    verdict = ev["verdict"]
    block = lambda codes: {"decision": policy_gate.BLOCK, "reason_codes": codes, "amount": ev.get("amount_remaining")}
    if verdict == "CONTRADICTED":
        case["outcome"] = "CLAIM_CONTRADICTED"
        case["gate"] = block([c for c in ev["codes"] if c in claim_verifier.CONTRADICTING_CODES])
        _event(case, "POLICY_BLOCK", {"reason_codes": case["gate"]["reason_codes"]})
        _alert_order_owners(case)
    elif verdict == "UNVERIFIABLE":
        case["outcome"] = "DISPUTE_RAISED"
        case["dispute"] = {"ref": "DSP" + security.sha256_hex(case["txn_id"] + now.isoformat())[:10].upper(),
                           "raised_at": now.isoformat(), "with": case["counterparty"],
                           "respond_by": (now + timedelta(hours=config.MERCHANT_DISPUTE_SLA_HOURS)).isoformat()}
        _event(case, "MERCHANT_DISPUTE_RAISED", case["dispute"])
    elif verdict == "NOT_DUE":
        case["outcome"] = "NO_REFUND_DUE"
        case["gate"] = block(["NO_REFUND_DUE_UNDER_POLICY"])
        _event(case, "MERCHANT_RECORD_NO_REFUND_DUE", {"status": ev["order"]["status"], "codes": ev["codes"]})
    elif verdict == "ALREADY_REFUNDED":
        case["outcome"] = "ALREADY_REFUNDED"
        case["gate"] = block(["ALREADY_REFUNDED"])
        r = next(r for r in ev["order"]["refunds"] if r["status"] == "CREDITED")
        _event(case, "ALREADY_REFUNDED", {"amount": ev["amount_credited"], "utr": r["utr"], "credited_at": r["credited_at"]})
    elif verdict == "NEEDS_REVIEW":
        case["outcome"] = "MANUAL_REVIEW"
        case["gate"] = block(["CHARGEBACK_OPEN"])
        _event(case, "POLICY_BLOCK", {"reason_codes": ["CHARGEBACK_OPEN"]})
    else:
        o = ev["order"]
        amount = ev["amount_pending"] if verdict == "IN_FLIGHT" else ev["amount_remaining"]
        by_when = max(datetime.fromisoformat(ev["deadline_iso"]), now + timedelta(hours=config.CUSTOMER_UPDATE_WINDOW_HOURS))
        draft = {"what": f"{inr(amount)} refund from {o['merchant_name']} for {o['order_label']} {o['order_ref']}, "
                         f"credited to the original payment source",
                 "by_when": by_when.isoformat(), "who": f"{o['merchant_name']} (pays) · VAADA chases it to completion",
                 "proof_check_id": "MERCHANT_REFUND_UTR_CREDITED", "narrower_controllable_commitment": None,
                 "basis": f"{ev['category_label']} policy: {ev['sla_days']} days from {o['status'].lower().replace('_', ' ')}"}
        case["expected_refund"] = amount
        case["promise"] = {"draft": draft, "head3": None, "created_at": now.isoformat()}
        _transition(case, "PROMISE_ACTIVE", "PROMISE_COMPILED", {"what": draft["what"], "by_when": draft["by_when"],
                    "proof_check_id": draft["proof_check_id"]})
        if verdict == "IN_FLIGHT":
            r = next(r for r in o["refunds"] if r["status"] == "PENDING")
            case["execution"] = {"refund_api": {"refund_ref": r["refund_ref"], "status_code": None}, "amount": amount,
                                 "executed_at": None, "by": "merchant (already in flight)"}
            _transition(case, "AWAITING_PROOF", "MERCHANT_REFUND_IN_FLIGHT", {"refund_ref": r["refund_ref"], "amount": amount,
                        "proof_check_id": draft["proof_check_id"]})
        else:
            run_merchant_gate(case)


def _alert_order_owners(case: dict) -> None:
    """Someone quoted another account's booking: record it on the real owner's case (if they have one)."""
    for ref in case["claimed"].get("refs") or []:
        o = merchant_rails.order_by_ref(ref)
        if not o or o.customer_ref == case["customer"]["id"]:
            continue
        owner = ledger_db.load_case(o.payment_txn_id)
        if owner:
            _event(owner, "ORDER_CLAIMED_BY_OTHER_ACCOUNT", {"order_ref": ref, "claimed_in_case": case["txn_id"],
                                                             "action": "flagged to security; owner's refund unaffected"})
            ledger_db.save_case(owner)


def run_merchant_gate(case: dict, human_approval: dict | None = None) -> dict:
    case["evidence"] = ev = _merchant_evidence(case)          # fresh records at decision time
    res = policy_gate.execute_merchant_refund(case["txn_id"], ev["order"]["order_ref"], EXEC_TOKEN, ev,
                                              human_approval=human_approval, adversarial=case["injection"]["adversarial"])
    case["gate"] = {k: v for k, v in res.items() if k != "refund_api"}
    case["identity_trail"].append({"role": "role:scoped_executor", "capability": "request_merchant_refund", "ok": True})
    if res.get("executed"):
        case["expected_refund"] = res["amount"]
        case["execution"] = {"refund_api": res["refund_api"], "idempotency_key": res["idempotency_key"], "amount": res["amount"],
                             "executed_at": clock.now().isoformat(), "by": "human_approval" if human_approval else "autonomous"}
        _transition(case, "ACTION_EXECUTED", "MERCHANT_REFUND_REQUESTED_202", {"refund_ref": res["refund_api"]["refund_ref"],
                    "amount": res["amount"], "idempotency_key": res["idempotency_key"]})
        _transition(case, "AWAITING_PROOF", "AWAITING_REFUND_CREDIT", {"proof_check_id": "MERCHANT_REFUND_UTR_CREDITED"})
    elif res["decision"] == policy_gate.HOLD and not case.get("approval"):
        req = policy_gate.issue_approval_request(case["txn_id"], "request_merchant_refund", res["amount"], config.PRIMARY_APPROVER)
        o = ev["order"]
        brief = (f"Ask {o['merchant_name']} to refund {inr(res['amount'])} for {o['order_label']} {o['order_ref']}. "
                 f"Records verified ({ev['verdict'].lower()}); held because: {', '.join(res['reason_codes'])}.")
        case["approval"] = {"status": "PENDING", "assigned_to": config.PRIMARY_APPROVER, "requested_at": clock.now().isoformat(),
                            "escalated": False, "authority_log": [], "request": req, "brief": brief, "reason_codes": res["reason_codes"]}
        _transition(case, "AWAITING_APPROVAL", "POLICY_HOLD", {"reason_codes": res["reason_codes"],
                    "approval_token_prefix": req["token"][:16], "assigned_to": config.PRIMARY_APPROVER})
    elif res["decision"] == policy_gate.BLOCK:
        _event(case, "POLICY_BLOCK", {"reason_codes": res["reason_codes"]})
    return res


def _finish_slow_path(txn_id: str) -> None:
    """Background: run the live CrewAI squad (bounded by CREW_TIMEOUT_S), then continue the pipeline."""
    with LOCK:
        case = ledger_db.load_case(txn_id)
    if not case:
        return
    claim = ClaimedContext(**{k: v for k, v in case["claimed"].items() if k != "untrusted"})
    v = verified_from_row(bank.get(txn_id))
    now = clock.now()
    rbi = policy_gate.evaluate_rbi_tat_compliance(v.tx_type, v.failure_stage, datetime.fromisoformat(v.txn_timestamp), now)
    crew = vaada_crew.run_slow_path(claim, v, case["nirnay"], rbi, now, allow_live=True, timeout_s=config.CREW_TIMEOUT_S)
    with LOCK:
        case = ledger_db.load_case(txn_id)
        if not case or case.get("promise"):          # already finished (e.g. reset raced us)
            return
        _event(case, "SPECIALISTS_REPORTED", {"mode": crew["mode"], "llm_calls": crew["llm_calls"], "error": crew.get("error"),
                                              "overrides": [o["field"] for o in crew.get("validation_overrides", [])]})
        _promise_and_gate(case, crew)
        case["triage"] = triage_summary(case)
        _refresh(case)
        ledger_db.save_case(case)
        _notify_case(case, _ack(case), "ack")


def _risk_review(case: dict) -> bool:
    return bool(((case.get("evidence") or {}).get("risk") or {}).get("review_required"))


def run_gate(case: dict, human_approval: dict | None = None) -> dict:
    if case.get("kind") == "MERCHANT_REFUND":
        return run_merchant_gate(case, human_approval)
    txn, d = case["txn_id"], case["nirnay"]
    res = policy_gate.execute_action(txn, "execute_refund", EXEC_TOKEN, human_approval=human_approval,
                                     nirnay_route=d["overall_route"], nirnay_action=d["action_pred"],
                                     adversarial=case["injection"]["adversarial"],
                                     claim_ledger_mismatch=case["claim_vs_fact"]["claim_ledger_mismatch"],
                                     risk_review=_risk_review(case))
    case["gate"] = {k: v for k, v in res.items() if k != "refund_api"}
    case["identity_trail"].append({"role": "role:scoped_executor", "capability": "execute_refund", "ok": True})
    if res.get("executed"):
        case["execution"] = {"refund_api": res["refund_api"], "idempotency_key": res["idempotency_key"],
                             "executed_at": clock.now().isoformat(), "by": "human_approval" if human_approval else "agent_wallet"}
        _transition(case, "ACTION_EXECUTED", "REFUND_API_ACCEPTED_202", {"refund_ref": res["refund_api"]["refund_ref"],
                    "amount": res["amount"], "idempotency_key": res["idempotency_key"]})
        _transition(case, "AWAITING_PROOF", "AWAITING_SETTLED_UTR", {"proof_check_id": case["promise"]["draft"]["proof_check_id"]})
    elif res["decision"] == policy_gate.HOLD and not case.get("approval"):
        req = policy_gate.issue_approval_request(txn, "execute_refund", res["amount"], config.PRIMARY_APPROVER)
        brief = (case["crew"]["plan"]["approver_brief"] if case.get("crew") else
                 f"Approve {inr(res['amount'])} refund for {txn}. Held by policy gate: {', '.join(res['reason_codes'])}.")
        case["approval"] = {"status": "PENDING", "assigned_to": config.PRIMARY_APPROVER, "requested_at": clock.now().isoformat(),
                            "escalated": False, "authority_log": [], "request": req, "brief": brief, "reason_codes": res["reason_codes"]}
        _transition(case, "AWAITING_APPROVAL", "POLICY_HOLD", {"reason_codes": res["reason_codes"],
                    "approval_token_prefix": req["token"][:16], "assigned_to": config.PRIMARY_APPROVER})
    elif res["decision"] == policy_gate.BLOCK:
        _event(case, "POLICY_BLOCK", {"reason_codes": res["reason_codes"]})
    return res


# ------------------------------------------------------------------ queries
def _group(c: dict) -> str:
    if c.get("outcome") in ("CONTAINED", "CLAIM_CONTRADICTED"):
        return "blocked"
    if c.get("outcome") in ("NO_REFUND_DUE", "ALREADY_REFUNDED") or c["state"] == "VERIFIED_CLOSED":
        return "closed"
    if c.get("outcome") in ("REJECTED", "DISPUTE_RAISED", "MANUAL_REVIEW"):
        return "manual_review"
    if c["state"] == "AWAITING_APPROVAL":
        return "approval"
    if (c.get("gate") or {}).get("decision") == "BLOCK":
        return "manual_review"
    return "in_progress"


def _verify(case: dict) -> dict | None:
    if case.get("kind") != "MERCHANT_REFUND":
        return verifier.verify(case["txn_id"], AUDIT_TOKEN)
    o = (case.get("evidence") or {}).get("order")
    if not o or not case.get("expected_refund"):
        return None
    return verifier.verify_merchant_refund(o["order_ref"], case["txn_id"], case["expected_refund"], AUDIT_TOKEN)


def view_case(txn: str) -> dict | None:
    with LOCK:
        case = ledger_db.load_case(txn)
        if not case:
            return None
        _refresh(case)
        if case["state"] != "VERIFIED_CLOSED":
            case["verification"] = _verify(case)
        ledger_db.save_case(case)
        case["group"] = _group(case)
        case["chain"] = ledger_db.get_chain(txn)
        case["chain_valid"] = ledger_db.verify_chain(txn)
        return case


def all_cases() -> list[dict]:
    out = []
    for t in ledger_db.list_case_ids():
        c = view_case(t)
        if c:
            out.append(c)
    return sorted(out, key=lambda c: c["created_at"], reverse=True)


def case_summaries() -> list[dict]:
    res = []
    for c in all_cases():
        p = c.get("promise")
        res.append({"txn_id": c["txn_id"], "customer": c["customer"]["name"], "amount": c["verified"]["verified_amount"],
                    "tx_type": c["verified"]["tx_type"], "counterparty": c["counterparty"], "state": c["state"],
                    "outcome": c.get("outcome"), "group": c["group"], "risk": c["breach"]["risk_score"],
                    "triage": c["triage"], "assigned_to": (c.get("approval") or {}).get("assigned_to"),
                    "by_when": p["draft"]["by_when"] if p else None, "created_at": c["created_at"],
                    "rbi_breached": c["sla"]["is_breached"], "kind": c.get("kind"),
                    "crew_running": (c.get("crew") or {}).get("mode") == "RUNNING"})
    return res


def customer_cases(customer_id: str) -> list[dict]:
    return [c for c in all_cases() if c["customer"]["id"] == customer_id]


# ------------------------------------------------------------------ actions
class ActionError(Exception):
    pass


def _load(txn: str) -> dict:
    c = ledger_db.load_case(txn)
    if not c:
        raise ActionError(f"Case {txn} not found")
    return c


def approve(txn: str, role: str) -> dict:
    with LOCK:
        case = _load(txn)
        _refresh(case)
        ap = case.get("approval")
        if not ap or ap["status"] != "PENDING":
            raise ActionError("This case has no pending approval.")
        if role != ap["assigned_to"]:
            raise ActionError(f"Authority is currently held by {ap['assigned_to']}; {role} cannot approve.")
        run_gate(case, human_approval={"payload": ap["request"]["payload"], "token": ap["request"]["token"]})
        if not case.get("execution"):
            ledger_db.save_case(case)
            raise ActionError(f"Execution blocked by policy gate: {', '.join(case['gate']['reason_codes'])}")
        ap.update(status="APPROVED", decided_by=role, decided_at=clock.now().isoformat())
        _refresh(case)
        ledger_db.save_case(case)
        if case.get("kind") == "MERCHANT_REFUND":
            _notify_case(case, f"Approval mil gaya! {inr(case['expected_refund'])} ki refund request merchant ko bhej di gayi hai. "
                               f"Credit (UTR) confirm hote hi main aapko bataunga.", "update")
        else:
            _notify_case(case, f"Approval mil gaya! {inr(case['verified']['verified_amount'])} ka refund bank ko bhej diya gaya hai. "
                               f"Bank se UTR confirm hote hi main aapko bataunga.", "update")
        return case


def reject(txn: str, role: str) -> dict:
    with LOCK:
        case = _load(txn)
        _refresh(case)
        ap = case.get("approval")
        if not ap or ap["status"] != "PENDING":
            raise ActionError("This case has no pending approval.")
        if role != ap["assigned_to"]:
            raise ActionError(f"Authority is currently held by {ap['assigned_to']}; {role} cannot reject.")
        ap.update(status="REJECTED", decided_by=role, decided_at=clock.now().isoformat())
        case["outcome"] = "REJECTED"
        _event(case, "APPROVAL_REJECTED", {"by": role})
        ledger_db.save_case(case)
        _notify_case(case, status_text(case), "update")
        return case


def bank_settles(txn: str) -> dict:
    with LOCK:
        case = _load(txn)
        merchant = case.get("kind") == "MERCHANT_REFUND"
        if merchant:
            if case["state"] != "AWAITING_PROOF":
                raise ActionError("No merchant refund is pending for this case yet.")
            wh = merchant_rails.settle_refund(case["evidence"]["order"]["order_ref"])
        else:
            wh = bank.bank_webhook_settle(txn)
        if not wh["ok"]:
            raise ActionError("The bank has no pending refund for this case yet (it must be approved/executed first).")
        _event(case, "BANK_WEBHOOK_RECEIVED", {"utr": wh["receipt"]["utr"], "status": "CREDITED",
                                               "source": "merchant refund" if merchant else "bank refund"})
        ver = _verify(case)
        if ver["closable"]:
            _transition(case, "VERIFIED_CLOSED", "PROOF_VERIFIED", {"utr": ver["receipt"]["utr"], "checks": ver["checks"]})
            amt = case.get("expected_refund") or case["verified"]["verified_amount"]
            text = (f"Aapka {inr(amt)} ka refund bank account me credit ho gaya hai. "
                    f"UTR {ver['receipt']['utr']}. VAADA par bharosa karne ke liye dhanyavaad!")
            tts = sarvam_adapter.text_to_speech(text)
            case["verification"] = {**ver, "voice": {k: v for k, v in tts.items() if k != "audio_b64"}}
            _refresh(case)
            ledger_db.save_case(case)
            notify(case["customer"]["id"], text, kind="closure", case_id=txn, voice=True,
                   audio_b64=tts.get("audio_b64"), voice_mode=tts["mode"])
        else:
            ledger_db.save_case(case)
        return {"closed": ver["closable"], "verification": ver, "receipt": wh["receipt"]}


def duplicate_retry(txn: str) -> dict:
    with LOCK:
        case = _load(txn)
        d = case["nirnay"]
        if case.get("kind") == "MERCHANT_REFUND":
            ev = case["evidence"]
            if not ev.get("order"):
                raise ActionError("This case has no merchant order to refund.")
            # replay of the *same* request (stored evidence): only the idempotency lock stands in the way
            res = policy_gate.execute_merchant_refund(txn, ev["order"]["order_ref"], EXEC_TOKEN, ev,
                                                      adversarial=case["injection"]["adversarial"])
        else:
            res = policy_gate.execute_action(txn, "execute_refund", EXEC_TOKEN, nirnay_route=d["overall_route"],
                                             nirnay_action=d["action_pred"], adversarial=case["injection"]["adversarial"],
                                             claim_ledger_mismatch=case["claim_vs_fact"]["claim_ledger_mismatch"],
                                             risk_review=_risk_review(case))
        res = {k: v for k, v in res.items() if k != "refund_api"}
        case.setdefault("retries", []).append({"at": clock.now().isoformat(), **res})
        _event(case, "DUPLICATE_RETRY_ATTEMPT", {"reason_codes": res["reason_codes"], "executed": res.get("executed", False),
                                                 "idempotency_key": res.get("idempotency_key")})
        ledger_db.save_case(case)
        return res


def advance_time(hours: float) -> float:
    if hours <= 0 or hours > 240:
        raise ActionError("Hours must be between 0 and 240.")
    with LOCK:
        off = clock.advance(hours)
        for t in ledger_db.list_case_ids():
            view_case(t)
        return off


def reset() -> None:
    with LOCK:
        clock.reset()
        policy_gate.set_kill_switch(False)
        ledger_db.init_db(reset=True)
        mock_ledger.init_schema()
        bank.reset()
        for rec in scenarios.ledger_records():
            bank.seed(rec)
        merchant_rails.reset()
        for o in scenarios.merchant_orders():
            merchant_rails.seed(o["merchant_id"], o["payment_txn_id"], o["raw"], refund=o["refund"], chargeback=o["chargeback"])


def radar_tick() -> None:
    with LOCK:
        for t in ledger_db.list_case_ids():
            c = ledger_db.load_case(t)
            if c and c["state"] != "VERIFIED_CLOSED" and not c.get("outcome"):
                _refresh(c)
                ledger_db.save_case(c)
