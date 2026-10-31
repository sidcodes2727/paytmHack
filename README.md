# VAADA (वादा) — "A reply is not a resolution. No proof, no close."

Autonomous AI teammate for Indian fintech support (failed UPI / refund-not-received), built for the
Sarvam AI "Autonomous AI Teammates" track.

```
Untrusted claim → Nirnay (local CPU, τ*) → CrewAI squad (only on abstain) → Policy Gate → Human approver → Proof-of-Done verifier
```

## Run it

Backend (Python 3.11+; tested on 3.12):
```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload          # http://127.0.0.1:8000
```
First start generates the data, fits + calibrates Nirnay, solves τ*, writes `backend/benchmark_results.json`
(~3 s; cached afterwards by source hash) and seeds four sandbox customers with their transaction histories. Cases are created only when a customer chats.

Frontend:
```bash
cd frontend && npm install && npm run dev     # http://localhost:5173 (proxies /api → :8000)
```
Optional: put `SARVAM_API_KEY` in `backend/.env` (template: `backend/.env.example`) and restart. Without it, CrewAI runs its
deterministic local squad (`DETERMINISTIC_FALLBACK`), voice input uses the browser's speech recognition, and the voice
receipt is spoken by the browser. With it, the squad, STT and TTS run live on Sarvam. Live screenshot OCR additionally needs `SARVAM_OCR_URL`;
without it, screenshots are stored as untrusted evidence and never used for amounts.

## How to use it
The screen has three parts. **Customer phone** (left), **Ops console** (centre) and **Ops Copilot** (right, or the floating button on smaller screens).

1. **Pick a customer** at the top of the phone (each has a different transaction history) and **type, speak, or attach a screenshot** in your own words. VAADA works out which transaction you mean from that customer's ledger, or asks with tappable choices.
2. The case appears in the **Case inbox**. The case view tells the story in plain language: what happened, the promise made, the RBI clock, and what the customer claimed vs. what the bank ledger says.
3. Ask the **Copilot** anything: *"Why was this case routed this way?"*, *"How accurate is Nirnay?"*, *"Show cost curve"*. Model metrics and charts live in this chat, not on the dashboard.
4. **Sandbox** (header menu, or Copilot commands) simulates the outside world: *"fast-forward 18h"*, *"bank settles this refund"*, *"simulate duplicate retry"*, *"reset"*. Questions never trigger actions.
5. **Signed in as** switches between `FINANCE_PRIMARY` and `FINANCE_BACKUP_LEAD`. **Autopilot** is the global kill switch.

### Hero demo (~2 min)
1. Customer **Riya** → send *"Mera ₹1,499 ka merchant UPI payment fail ho gaya. Paise kat gaye, 6 din ho gaye, refund abhi tak nahi aaya."* She gets an instant reply. Gateway, bank and merchant records disagree, so the **CrewAI squad runs live on Sarvam** (≈25 s, hard-bounded to 120 s). The **policy corrects the LLM's draft** (it tends to promise far-off dates or name the bank as owner). Then the gate holds the case: *above the ₹500 autonomous limit*.
2. Copilot: *"fast-forward 18h"* → the approver SLA breaks, authority moves to the backup, and the customer gets a proactive update.
3. As `FINANCE_PRIMARY` the Approve button is locked. Switch to `FINANCE_BACKUP_LEAD` and approve → refund API **202**, but the case stays open: *no proof, no close*.
4. Copilot: *"bank settles this refund"* → the verifier checks the bank-signed UTR → **Verified closed**, and Riya gets a Hindi voice receipt (Sarvam Bulbul).
5. Other customers: **Kabir** (prompt injection → contained before any LLM), **Aman** ₹350 (fast path, 0 LLM calls; ask about his ₹120 payment → "no refund due"), **Neha** (voice note → refund; then *"simulate duplicate retry"* → blocked by the idempotency lock).

### Sarvam models used
`sarvam-105b` for chat/CrewAI, sent with `reasoning_effort: null` (it's a reasoning model; with thinking on, replies took 20 s+ and empty answers made CrewAI loop). `saaras:v3` in `translit` mode for STT, so voice arrives as Roman Hinglish; spoken amounts like *"teen sau pachaas"* are parsed to 350. `bulbul:v3` for TTS. Older `sarvam-m` / `bulbul:v2` are deprecated by Sarvam.

## What is measured vs. what is a policy constant
* Measured at runtime: τ*_issue / τ*_action, confidences, latencies, F1, ECE, Brier, coverage, false-auto-approval, cost curves.
* Policy constants (`backend/config.py`): cost matrix (4 vs 1), ₹500/₹5,000 wallet, 12 h approver SLA, 24 h update window, RBI T+1/T+5 and ₹100/day.
* Data is `SYNTHETIC_DEMO_DATA`. OOD split = unseen templates, typo/slang perturbations, unseen injection payloads. A separate 200-case
  in-distribution test set exists only to report the ID→OOD gap (the 900-case core split is 500/200/200 as specified).

## 3-minute pitch script
**0:00 — Problem.** "Every support bot says 'we've raised your refund' and closes the ticket. In UPI that's a promise nobody can audit. VAADA turns every case into a typed Promise Contract — What, By When, Who, Proof — and refuses to close without proof."

**0:30 — Nirnay & τ*.** "Stage 1 isn't an LLM. Nirnay is a calibrated scikit-learn/XGBoost engine that runs on CPU in about a millisecond. When should it abstain? We didn't guess a threshold: with a false route costing 4 and an abstention costing 1, we minimise empirical risk on a held-out calibration split. That solves for τ*." Ask the Copilot *"Show cost curve"* to plot the exact risk curve where τ* was chosen.

**1:15 — OOD honesty.** "We test on 200 cases with phrasing, spelling and attack payloads the model never saw. Both systems degrade; the keyword baseline degrades more, and Nirnay's calibrated abstention cuts expected cost and false auto-approvals because it knows what it doesn't know. Every number comes from `benchmark_results.json`, not from us." Ask the Copilot *"How accurate is Nirnay?"*, then *"Show calibration"*.

**1:45 — Hero case.** Type Riya's complaint on the phone. CrewAI (Sarvam) only wakes on abstain, and its draft is policed: show the *Policy corrected the LLM draft* box; RBI circular RBI/2019-20/67 says T+5 for P2M, so at day 6 we track ₹100/day exposure — owed by the liable bank/acquirer, not VAADA. The deterministic Policy Gate holds anything over ₹500, signs a 1-tap HMAC approval, and when the primary approver sleeps past SLA the Breach Radar transfers authority and messages the customer before we miss our own deadline.

**2:30 — Proof, not promises.** "The refund API said 202. Still `AWAITING_PROOF`. Only when the bank webhook posts a settled UTR and the isolated read-only verifier validates the signed receipt does the case close — and the customer gets a Hindi voice receipt via Sarvam Bulbul." Show the hash-chained audit trail, the injection case and the duplicate-retry lock.

**2:55 — Close.** "No proof, no close. That's VAADA."
