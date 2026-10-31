"""Ops Copilot: the operator's chat. Answers metrics / 'why' questions and runs sandbox controls.

Intent routing is deterministic (so actions are predictable and auditable). Free-form questions that match no
intent go to Sarvam chat, grounded on the case + benchmark summary, when SARVAM_API_KEY is set.
"""
from __future__ import annotations

import json
import re

import clock
import config
import ledger_db
import orchestrator as orc
import policy_gate
import sarvam_adapter

HELP_CHIPS = ["How accurate is Nirnay?", "Why was this case routed this way?", "Queue status", "Fast-forward 18h",
              "Bank settles this refund", "Simulate duplicate retry"]


def _pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def _reply(text: str, cards: list | None = None, chips: list | None = None, changed: bool = False) -> dict:
    return {"text": text, "cards": cards or [], "chips": chips or [], "changed": changed}


# ------------------------------------------------------------------ answers
def metrics(kind: str = "summary") -> dict:
    b = orc.STATE["bench"]
    ood = b["results"]["ood_test"]
    n, base = ood["nirnay"], ood["baseline"]
    th = b["thresholds"]
    meta = b["meta"]
    if kind == "cost":
        r = th["expected_cost_reduction"]["issue_ood"]
        return _reply(
            f"**How τ* was chosen.** False route costs **{th['cost_matrix']['c_error']:g}**, abstaining costs "
            f"**{th['cost_matrix']['c_abstain']:g}**. I swept τ over [{config.TAU_MIN}, {config.TAU_MAX}] on the held-out "
            f"calibration split and took the argmin of empirical risk:\n"
            f"- τ*_issue = **{th['tau_star_issue']:.2f}**, τ*_action = **{th['tau_star_action']:.2f}** "
            f"(Chow-rule reference 1 − 1/4 = {th['cost_matrix']['chow_rule_reference_tau']:.2f})\n"
            f"- On unseen OOD cases the issue-head risk is **{r['risk_at_tau_star']:.0f}** at τ* vs "
            f"**{r['risk_no_abstention']:.0f}** with no abstention (**{_pct(r['reduction_vs_no_abstention'])} lower**).",
            cards=[{"type": "chart", "chart": "cost_curve"}], chips=["Show calibration", "Show OOD generalisation"])
    if kind == "calibration":
        c = n["calibration"]
        return _reply(
            f"**Calibration on OOD (issue head).** ECE **{c['issue_ece']:.3f}** (uncalibrated {c['issue_ece_uncalibrated']:.3f}), "
            f"Brier **{c['issue_brier']:.3f}** (uncalibrated {c['issue_brier_uncalibrated']:.3f}). Action head ECE "
            f"**{c['action_ece']:.3f}**. Points under the diagonal are over-confident bins.",
            cards=[{"type": "chart", "chart": "reliability"}], chips=["Show cost curve", "How accurate is Nirnay?"])
    if kind == "gap":
        g = b["generalisation_gap"]
        return _reply(
            f"**In-distribution → OOD (issue macro-F1).** Nirnay {g['nirnay_issue_macro_f1']['id']:.3f} → "
            f"**{g['nirnay_issue_macro_f1']['ood']:.3f}**; keyword baseline {g['baseline_issue_macro_f1']['id']:.3f} → "
            f"**{g['baseline_issue_macro_f1']['ood']:.3f}**. Both degrade on unseen phrasing; calibrated abstention is what "
            f"keeps Nirnay's mistakes off the fast path.",
            cards=[{"type": "chart", "chart": "gap"}], chips=["Show cost curve", "Show calibration"])
    rows = [
        ["Issue macro-F1", f"{base['issue_macro_f1']:.3f}", f"{n['issue_macro_f1']:.3f}"],
        ["False auto-approvals (of all cases)", _pct(base["system"]["false_auto_approval_rate"]), _pct(n["system"]["false_auto_approval_rate"])],
        ["False closures", _pct(base["false_closure_rate"]), _pct(n["false_closure_rate"])],
        [f"Cost ({th['cost_matrix']['c_error']:g}×wrong + {th['cost_matrix']['c_abstain']:g}×abstain)",
         f"{base['system']['empirical_cost']:.0f}", f"{n['system']['empirical_cost']:.0f}"],
        ["Handled without an LLM", "—", f"{n['llm_calls_avoided']} / {n['llm_calls_llm_first_baseline']}"],
        ["Accuracy when Nirnay acts alone", "—", _pct(n["selective_accuracy_on_fast_path"])],
        ["CPU latency p50 / p95", "—", f"{n['latency_ms']['p50']:.2f} / {n['latency_ms']['p95']:.2f} ms"],
    ]
    return _reply(
        f"Measured on **{meta['split_sizes']['ood_test']} unseen out-of-distribution cases** "
        f"({meta['data_label']}: new phrasing, typos/slang, unseen injection payloads). Nothing here is hardcoded — "
        f"it's read from `benchmark_results.json`, regenerated whenever the model code changes.",
        cards=[{"type": "table", "columns": ["Metric", "Keyword baseline", "VAADA"], "rows": rows}],
        chips=["Show cost curve", "Show calibration", "Show OOD generalisation"])


def explain(case: dict) -> dict:
    n, v, r = case["nirnay"], case["verified"], case["rbi"]
    lines = [f"**{case['txn_id']}** · ₹{v['verified_amount']:,.0f} {v['tx_type']} · {case['customer']['name']} → {case['counterparty']}"]
    lines.append(f"- **Nirnay** (local CPU, {n['latency_ms']:.2f} ms, {n['llm_calls']} LLM calls): issue `{n['issue_pred']}` at "
                 f"{_pct(n['issue_confidence'])} vs τ* {n['tau_star_issue']:.2f}; action `{n['action_pred']}` at "
                 f"{_pct(n['action_confidence'])} vs τ* {n['tau_star_action']:.2f}.")
    lines.append(f"- **Route:** {case['triage']['label']} — {case['triage']['text']}")
    cr = case.get("crew")
    if cr and cr["mode"] == "RUNNING":
        lines.append(f"- **Specialist squad:** running live on Sarvam right now (bounded to {cr['timeout_s']}s, then deterministic fallback).")
    elif cr:
        lines.append(f"- **Specialist squad** ({cr['mode']}): {cr['diagnosis']['root_cause']}")
        for o in cr.get("validation_overrides", []):
            lines.append(f"  - Policy corrected the LLM's `{o['field']}`: {o['reason']}.")
        if cr.get("error"):
            lines.append(f"  - Live squad note: {cr['error'][:140]}")
    if case["claim_vs_fact"]["mismatches"]:
        m = case["claim_vs_fact"]["mismatches"][0]
        lines.append(f"- **Claim vs ledger:** customer said {m['claimed']}, ledger says {m['ledger']}.")
    if case.get("gate"):
        lines.append(f"- **Policy gate:** {case['gate']['decision']} — {', '.join(case['gate']['reason_codes'])}")
    lines.append(f"- **RBI {r['tat_rule']}:** {r['days_elapsed']} days elapsed, "
                 f"{'**breached**' if r['is_tat_breached'] else 'within ceiling'}; exposure ₹{r['accrued_compensation_inr']} "
                 f"(liable: {r['liability_entity']}, not VAADA).")
    if case.get("promise"):
        lines.append(f"- **Closes only on proof:** `{case['promise']['draft']['proof_check_id']}`.")
    chips = []
    if case["state"] == "AWAITING_APPROVAL":
        chips = ["Fast-forward 18h", "Approve this refund"]
    elif case["state"] == "AWAITING_PROOF":
        chips = ["Bank settles this refund", "Simulate duplicate retry"]
    return _reply("\n".join(lines), chips=chips + ["Show audit trail"])


def queue_status() -> dict:
    cs = orc.case_summaries()
    groups = {}
    for c in cs:
        groups[c["group"]] = groups.get(c["group"], 0) + 1
    exposure = sum(orc.view_case(c["txn_id"])["rbi"]["accrued_compensation_inr"] for c in cs
                   if c["group"] not in ("closed", "blocked"))
    spent = ledger_db.wallet_spent_last_hour()
    rows = [["Open cases", str(len(cs) - groups.get("closed", 0))], ["Needs approval", str(groups.get("approval", 0))],
            ["Awaiting bank proof", str(sum(1 for c in cs if c["state"] == "AWAITING_PROOF"))],
            ["Blocked (security)", str(groups.get("blocked", 0))], ["Closed", str(groups.get("closed", 0))],
            ["Agent wallet left this hour", f"₹{config.AGENT_WALLET_HOURLY_INR - spent:,.0f} of ₹{config.AGENT_WALLET_HOURLY_INR:,}"],
            ["RBI compensation exposure tracked", f"₹{exposure:,}"],
            ["Autopilot", "OFF (kill switch active)" if policy_gate.KILL_SWITCH_ACTIVE else "ON"],
            ["Sandbox clock", f"+{clock.offset_hours():g}h"]]
    return _reply("Here's the queue right now:", cards=[{"type": "table", "columns": ["", ""], "rows": rows}])


def audit(case: dict) -> dict:
    ok = ledger_db.verify_chain(case["txn_id"])
    rows = [[str(e["seq"]), e["event"], f"{e['current_hash'][:12]}…"] for e in case["chain"]]
    return _reply(f"Audit trail for **{case['txn_id']}**: {len(rows)} entries, hash chain **{'VALID' if ok else 'BROKEN'}**. "
                  f"Each hash = SHA256(prev_hash + payload + timestamp), so editing any entry breaks every later hash.",
                  cards=[{"type": "table", "columns": ["#", "Event", "Hash"], "rows": rows}])


# ------------------------------------------------------------------ router
def handle(text: str, case_id: str | None, role: str) -> dict:
    t = text.lower().strip()
    case = orc.view_case(case_id) if case_id else None
    # Questions never trigger actions: "what should finance double-check?" must not fire a duplicate retry.
    question = "?" in t or bool(re.match(r"(what|why|how|should|can|could|would|is|are|does|do|did|when|which|who|kya|kaise|kyun|kab)\b", t))
    need_case = "Select a case in the inbox first."
    try:
        if re.search(r"^(help|\?)$|what can you", t):
            return _reply("I can explain cases, report model metrics, and drive the sandbox:", chips=HELP_CHIPS)
        if re.search(r"cost|tau|τ|threshold", t):
            return metrics("cost")
        if re.search(r"calibrat|ece|brier|reliab", t):
            return metrics("calibration")
        if re.search(r"ood|generali|gap|unseen", t):
            return metrics("gap")
        if re.search(r"metric|accura|benchmark|latency|f1|how good|performance|kitna|precision|recall", t):
            return metrics()
        m = re.search(r"(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|ghante|d|day|days|din)\b", t)
        if m and not question and re.search(r"forward|advance|skip|jump|travel|\+|aage|later|move", t):
            hours = float(m.group(1)) * (24 if m.group(2).startswith(("d", "din")) else 1)
            off = orc.advance_time(hours)
            return _reply(f"Sandbox clock moved **+{hours:g}h** (now +{off:g}h, {clock.now().strftime('%d %b %I:%M %p')}). "
                          f"Breach Radar re-evaluated every case: SLAs, escalations and RBI clocks are updated.", changed=True,
                          chips=["Queue status", "Why was this case routed this way?"])
        if re.search(r"autopilot|kill ?switch", t):
            off = bool(re.search(r"autopilot\s*(off|band|stop)|kill ?switch\s*(on|activ|enable)|pause", t))
            on = bool(re.search(r"autopilot\s*(on|resume|start)|kill ?switch\s*(off|deactiv|disable)", t))
            if (off or on) and not question:
                policy_gate.set_kill_switch(off)
            state = "OFF — every refund now needs a human" if policy_gate.KILL_SWITCH_ACTIVE else "ON — refunds ≤ ₹500 can run autonomously"
            return _reply(f"Autopilot is **{state}**.", changed=off or on)
        if re.search(r"queue|status|overview|summary|wallet|exposure", t):
            return queue_status()
        if re.search(r"^reset\b|reset (the )?sandbox", t) and not question:
            orc.reset()
            return _reply("Sandbox reset: ledger re-seeded, all cases and chats cleared, clock back to +0h.", changed=True)
        if not case:
            if re.search(r"why|explain|kyun|reason|settle|webhook|utr|retry|duplicate|approve|reject|audit|chain", t):
                return _reply(need_case)
        else:
            if not question and re.search(r"settle|webhook|bank confirm", t):
                r = orc.bank_settles(case["txn_id"])
                return _reply(f"Bank posted a settled credit, UTR **{r['receipt']['utr']}**. The independent verifier checked "
                              f"the signed receipt → **{'VERIFIED CLOSED' if r['closed'] else 'still blocked'}**. "
                              f"Customer got a Hindi voice receipt.", changed=True)
            if not question and re.search(r"\bretry\b|duplicate retry|simulate duplicate|replay", t):
                r = orc.duplicate_retry(case["txn_id"])
                msg = ("Second refund **blocked** by the SHA-256 idempotency lock "
                       f"(`{(r.get('idempotency_key') or '')[:16]}…`). No double payout."
                       if "DUPLICATE_ACTION_BLOCKED" in r["reason_codes"] else
                       f"Retry did not execute: {', '.join(r['reason_codes'])} (no refund has run yet, so there's nothing to duplicate).")
                return _reply(msg, changed=True)
            if not question and re.search(r"^approve\b|approve (this|the|as)\b", t):
                who = config.BACKUP_APPROVER if "backup" in t else config.PRIMARY_APPROVER if "primary" in t else role
                orc.approve(case["txn_id"], who)
                return _reply(f"Approved as **{who}**. HMAC token verified → refund API answered **202 Accepted**. "
                              f"Case is now **AWAITING_PROOF**: it will not close until the bank confirms a settled UTR.",
                              changed=True, chips=["Bank settles this refund", "Simulate duplicate retry"])
            if not question and re.search(r"^reject\b|reject (this|the)\b", t):
                orc.reject(case["txn_id"], role)
                return _reply("Rejected. Case moved to manual review and the customer was told.", changed=True)
            if re.search(r"audit|chain|hash|tamper", t):
                return audit(case)
            if re.search(r"why|explain|kyun|reason|route|held|block|what happened", t):
                return explain(case)
    except orc.ActionError as e:
        return _reply(f"⚠️ {e}")

    if sarvam_adapter.has_key():
        ctx = {"case": {k: case[k] for k in ("txn_id", "state", "nirnay", "gate", "rbi", "triage", "promise")} if case else None,
               "metrics": metrics()["cards"][0]["rows"]}
        r = sarvam_adapter.chat([
            {"role": "system", "content": "You are VAADA Ops Copilot for a UPI support team. Answer briefly and only from the "
                                          "JSON context. If the context does not contain the answer, say so."},
            {"role": "user", "content": f"Context: {json.dumps(ctx, default=str)[:6000]}\n\nQuestion: {text}"}])
        if r.get("content"):
            return _reply(r["content"] + "\n\n(Answered by Sarvam, grounded on this case and the benchmark data.)")
    return _reply("I didn't catch that. Try one of these:", chips=HELP_CHIPS)
