"""Stage 4 - event-driven Breach Radar: risk score, RBI countdown, approver escalation, proactive updates."""
from datetime import datetime

import clock
from config import (APPROVER_SLA_HOURS, BACKUP_APPROVER, CUSTOMER_UPDATE_WINDOW_HOURS, PRIMARY_APPROVER,
                    PROACTIVE_UPDATE_REMAINING_FRACTION)
from policy_gate import evaluate_rbi_tat_compliance


def hinglish_update(case: dict, rbi: dict) -> str:
    amt = int(case["verified"]["verified_amount"])
    return (f"Namaste! Aapke ₹{amt:,} wale case ({case['txn_id']}) par kaam jaari hai. Refund approval aur bank "
            f"processing chal rahi hai; hum aapko agla update {int(CUSTOMER_UPDATE_WINDOW_HOURS)} ghante ke andar de denge. "
            f"Settlement bank ke haath me hai, par hum har kadam track kar rahe hain. — VAADA")


def refresh(case: dict, append_event, notify=None) -> dict:
    """Recompute dynamic fields on a case. `append_event(case, name, data)` records hash-chained events."""
    now = clock.now()
    v = case["verified"]
    rbi = evaluate_rbi_tat_compliance(v["tx_type"], v["failure_stage"], datetime.fromisoformat(v["txn_timestamp"]), now)
    case["rbi"] = rbi
    closed = case["state"] == "VERIFIED_CLOSED" or case.get("outcome") is not None

    ap = case.get("approval")
    if ap and ap["status"] == "PENDING" and not closed:
        requested = datetime.fromisoformat(ap["requested_at"])
        waited = (now - requested).total_seconds() / 3600
        ap["hours_waiting"] = waited
        ap["sla_hours"] = APPROVER_SLA_HOURS
        if waited >= APPROVER_SLA_HOURS and ap["assigned_to"] == PRIMARY_APPROVER:
            ap["assigned_to"] = BACKUP_APPROVER
            ap["escalated"] = True
            ap["escalated_at"] = now.isoformat()
            ap["authority_log"].append({"at": now.isoformat(), "from": PRIMARY_APPROVER, "to": BACKUP_APPROVER,
                                        "reason": f"No action within {APPROVER_SLA_HOURS:g}h SLA"})
            append_event(case, "APPROVER_ESCALATED", {"from": PRIMARY_APPROVER, "to": BACKUP_APPROVER, "waited_h": round(waited, 2)})

    created = datetime.fromisoformat(case["created_at"])
    elapsed_h = (now - created).total_seconds() / 3600
    window = CUSTOMER_UPDATE_WINDOW_HOURS
    remaining_frac = max(0.0, (window - elapsed_h) / window)
    if not closed and not case.get("proactive_update_sent") and remaining_frac <= PROACTIVE_UPDATE_REMAINING_FRACTION \
            and elapsed_h < window:
        msg = hinglish_update(case, rbi)
        case["proactive_update_sent"] = {"at": now.isoformat(), "text": msg}
        if notify:
            notify(case, msg, "proactive_update")
        append_event(case, "PROACTIVE_CUSTOMER_UPDATE", {"remaining_fraction": round(remaining_frac, 3)})

    promise_progress = min(1.0, elapsed_h / window)
    t0 = datetime.fromisoformat(v["txn_timestamp"])
    span = (datetime.fromisoformat(rbi["tat_deadline_iso"]) - t0).total_seconds()
    rbi_progress = min(1.0, max(0.0, (now - t0).total_seconds() / span))
    overdue = 1.0 if (ap and ap["status"] == "PENDING" and ap.get("hours_waiting", 0) >= APPROVER_SLA_HOURS) else 0.0
    base = max(promise_progress, rbi_progress)
    score = 0 if closed else round(100 * min(1.0, base * (1 + 0.25 * overdue)))
    case["breach"] = {"risk_score": score, "promise_window_progress": promise_progress, "rbi_tat_progress": rbi_progress,
                      "approval_overdue": bool(overdue), "elapsed_hours": elapsed_h, "virtual_now": now.isoformat(),
                      "time_offset_hours": clock.offset_hours(),
                      "formula": "100 * min(1, max(promise_window_progress, rbi_tat_progress) * (1 + 0.25*approval_overdue)); 0 when closed"}
    return case
