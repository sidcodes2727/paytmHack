"""Case pipeline: Nirnay -> CrewAI (on abstain) -> Policy Gate -> Breach Radar -> Proof-of-Done verifier.

Also owns customer notifications, so every state change the customer should hear about lands in their chat.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime

import breach_radar
import clock
import config
import data_and_nirnay as dn
import ledger_db
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
def status_text(case: dict) -> str:
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
    if "SCREENSHOT_LEDGER_MISMATCH" in codes:
        m = case["claim_vs_fact"]["mismatches"][0]
        return (f"Aapne jo amount bataya (₹{m['claimed']:,.0f}) wo bank records ({inr(m['ledger'])}) se match nahi karta. "
                f"Case specialist review me bheja gaya hai; 24 ghante me update denge.")
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


def triage_summary(case: dict) -> dict:
    n = case["nirnay"]
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


# ------------------------------------------------------------------ pipeline
def open_case(customer_id: str, txn_id: str, text: str, *, voice_transcript: str = "", voice_mode: str | None = None,
              ocr: dict | None = None, attachment: dict | None = None) -> dict:
    model: dn.Nirnay = STATE["model"]
    row = bank.get(txn_id)
    v = verified_from_row(row)
    now = clock.now()

    claim = ClaimedContext(raw_text=text, voice_transcript=voice_transcript, voice_mode=voice_mode,
                           ocr=OcrClaim(**ocr) if ocr else None)
    scan = scan_for_injection(claim)
    cvf = claim_vs_fact(claim, v)
    disc = multi_system_discrepancy(v)
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


def run_gate(case: dict, human_approval: dict | None = None) -> dict:
    txn, d = case["txn_id"], case["nirnay"]
    res = policy_gate.execute_action(txn, "execute_refund", EXEC_TOKEN, human_approval=human_approval,
                                     nirnay_route=d["overall_route"], nirnay_action=d["action_pred"],
                                     adversarial=case["injection"]["adversarial"],
                                     claim_ledger_mismatch=case["claim_vs_fact"]["claim_ledger_mismatch"])
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
    if c.get("outcome") == "CONTAINED":
        return "blocked"
    if c.get("outcome") in ("NO_REFUND_DUE",) or c["state"] == "VERIFIED_CLOSED":
        return "closed"
    if c.get("outcome") == "REJECTED":
        return "manual_review"
    if c["state"] == "AWAITING_APPROVAL":
        return "approval"
    if (c.get("gate") or {}).get("decision") == "BLOCK":
        return "manual_review"
    return "in_progress"


def view_case(txn: str) -> dict | None:
    with LOCK:
        case = ledger_db.load_case(txn)
        if not case:
            return None
        _refresh(case)
        if case["state"] != "VERIFIED_CLOSED":
            case["verification"] = verifier.verify(txn, AUDIT_TOKEN)
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
                    "rbi_breached": c["rbi"]["is_tat_breached"],
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
        wh = bank.bank_webhook_settle(txn)
        if not wh["ok"]:
            raise ActionError("The bank has no pending refund for this case yet (it must be approved/executed first).")
        _event(case, "BANK_WEBHOOK_RECEIVED", {"utr": wh["receipt"]["utr"], "status": "CREDITED"})
        ver = verifier.verify(txn, AUDIT_TOKEN)
        if ver["closable"]:
            _transition(case, "VERIFIED_CLOSED", "PROOF_VERIFIED", {"utr": ver["receipt"]["utr"], "checks": ver["checks"]})
            text = (f"Aapka {inr(case['verified']['verified_amount'])} ka refund bank account me credit ho gaya hai. "
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
        res = policy_gate.execute_action(txn, "execute_refund", EXEC_TOKEN, nirnay_route=d["overall_route"],
                                         nirnay_action=d["action_pred"], adversarial=case["injection"]["adversarial"],
                                         claim_ledger_mismatch=case["claim_vs_fact"]["claim_ledger_mismatch"])
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


def radar_tick() -> None:
    with LOCK:
        for t in ledger_db.list_case_ids():
            c = ledger_db.load_case(t)
            if c and c["state"] != "VERIFIED_CLOSED" and not c.get("outcome"):
                _refresh(c)
                ledger_db.save_case(c)
