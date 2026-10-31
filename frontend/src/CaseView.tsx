import { ReactNode, useState } from "react";
import {
  AlertTriangle, ArrowUpRight, BadgeCheck, Ban, Banknote, CheckCircle2, ChevronDown, Clock, FileCheck2, Fingerprint,
  Landmark, Lock, MessageSquare, Repeat, Scale, ShieldAlert, ShieldCheck, UserCheck, Users,
} from "lucide-react";
import { api, inr, Json, reason, when } from "./api";
import { statusLabel } from "./Inbox";
import { Field, Panel, Pill } from "./ui";

type Step = { icon: ReactNode; title: string; body?: ReactNode; tone: string };

function step(e: Json, c: Json): Step {
  const d = JSON.parse(e.payload).data ?? {};
  const codes = (x: string[] = []) => x.map(reason).join(" · ");
  switch (e.event) {
    case "OPEN_TRIAGED":
      return { icon: <MessageSquare size={14} />, tone: "blue", title: "Case opened from customer chat",
        body: <><span className="italic text-slate-400">“{d.customer_text}”</span><div className="mt-1">{d.nirnay_route === "fast_path" ? "Nirnay triaged it locally with high confidence." : "Nirnay flagged it for specialist review."}</div></> };
    case "SPECIALISTS_ENGAGED":
      return { icon: <Users size={14} />, tone: "amber", title: "Specialist squad engaged", body: `CrewAI agents on Sarvam (bounded to ${d.timeout_s}s). Customer told to expect an update.` };
    case "SPECIALISTS_REPORTED":
      return { icon: <Users size={14} />, tone: d.mode === "CREWAI_SARVAM_LIVE" ? "green" : "slate",
        title: d.mode === "CREWAI_SARVAM_LIVE" ? "Specialist squad reported (live)" : "Specialist review completed (local fallback)",
        body: d.overrides?.length ? `Policy corrected ${d.overrides.length} field${d.overrides.length > 1 ? "s" : ""} in the LLM draft: ${d.overrides.join(", ")}.` : d.error ? `Live squad unavailable: ${String(d.error).slice(0, 90)}` : undefined };
    case "PROMISE_COMPILED":
      return { icon: <FileCheck2 size={14} />, tone: "green", title: "Promise made to customer", body: <>{d.what} · by {when(d.by_when)}</> };
    case "POLICY_HOLD":
      return { icon: <Lock size={14} />, tone: "amber", title: "Held for human approval", body: codes(d.reason_codes) };
    case "APPROVER_ESCALATED":
      return { icon: <Users size={14} />, tone: "red", title: "Escalated to backup approver", body: `${d.from} didn't act within SLA; authority moved to ${d.to}.` };
    case "PROACTIVE_CUSTOMER_UPDATE":
      return { icon: <MessageSquare size={14} />, tone: "blue", title: "Customer updated before the deadline" };
    case "REFUND_API_ACCEPTED_202":
      return { icon: <Banknote size={14} />, tone: "green", title: `Refund of ${inr(d.amount)} sent to bank`, body: "Bank API answered 202 Accepted. That is not settlement yet." };
    case "AWAITING_SETTLED_UTR":
      return { icon: <Clock size={14} />, tone: "slate", title: "Waiting for proof", body: `Needs ${d.proof_check_id}` };
    case "DUPLICATE_RETRY_ATTEMPT":
      return d.reason_codes?.includes("DUPLICATE_ACTION_BLOCKED")
        ? { icon: <Repeat size={14} />, tone: "amber", title: "Duplicate refund attempt blocked", body: "SHA-256 idempotency lock prevented a second payout." }
        : { icon: <Repeat size={14} />, tone: "slate", title: "Retry not executed", body: codes(d.reason_codes) };
    case "BANK_WEBHOOK_RECEIVED":
      return { icon: <Landmark size={14} />, tone: "green", title: "Bank confirmed the credit", body: <>UTR <span className="font-mono">{d.utr}</span></> };
    case "PROOF_VERIFIED":
      return { icon: <BadgeCheck size={14} />, tone: "green", title: "Verified & closed", body: "Independent auditor checked the signed bank receipt." };
    case "ADVERSARIAL_INPUT_CONTAINED":
      return { icon: <ShieldAlert size={14} />, tone: "red", title: "Prompt injection contained", body: "Text never reached an LLM; no payout possible." };
    case "LEDGER_SHOWS_SUCCESS":
      return { icon: <CheckCircle2 size={14} />, tone: "slate", title: "Ledger shows the payment succeeded", body: "Customer informed that no refund is due." };
    case "POLICY_BLOCK":
      return { icon: <Ban size={14} />, tone: "red", title: "Blocked by policy gate", body: codes(d.reason_codes) };
    case "APPROVAL_REJECTED":
      return { icon: <Ban size={14} />, tone: "red", title: `Rejected by ${d.by}`, body: "Moved to manual review." };
    default:
      return { icon: <Clock size={14} />, tone: "slate", title: e.event };
  }
}

const dot: Record<string, string> = {
  green: "bg-emerald-500/15 text-emerald-300", amber: "bg-amber-500/15 text-amber-300", red: "bg-rose-500/15 text-rose-300",
  blue: "bg-sky-500/15 text-sky-300", slate: "bg-white/5 text-slate-400",
};

function Brief({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2 text-sm">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1 text-xs text-amber-200/80 hover:text-amber-100">
        <ChevronDown size={13} className={open ? "rotate-180" : ""} /> {live ? "Specialist squad's approver brief (Sarvam)" : "Approver brief"}
      </button>
      {open && <p className="mt-2 rounded-xl bg-black/20 p-3 leading-relaxed text-slate-300">{text}</p>}
    </div>
  );
}

function ActionBanner({ c, role, refresh, toast, ask }: { c: Json; role: string; refresh: () => void; toast: (s: string) => void; ask: (q: string) => void }) {
  const ap = c.approval;
  const [busy, setBusy] = useState(false);
  const act = async (fn: () => Promise<Json>, ok: string) => {
    setBusy(true);
    try { await fn(); toast(ok); } catch (e) { toast((e as Error).message); }
    setBusy(false); refresh();
  };

  if (c.crew?.mode === "RUNNING")
    return (
      <div className="rounded-2xl border border-amber-500/20 bg-amber-500/[0.05] p-5">
        <div className="flex items-center gap-2 font-semibold text-amber-100"><Users size={18} className="animate-pulse" /> Specialist squad is investigating…</div>
        <p className="mt-1 text-sm text-amber-100/70">Three CrewAI agents (investigator, promise compiler, planner) are reconciling gateway, bank and merchant records on Sarvam. Bounded to {c.crew.timeout_s}s, then the deterministic squad takes over. The customer has already been told to expect an update.</p>
      </div>
    );
  if (c.outcome === "CONTAINED")
    return (
      <div className="rounded-2xl border border-rose-500/25 bg-rose-500/[0.06] p-5">
        <div className="flex items-center gap-2 font-semibold text-rose-200"><ShieldAlert size={18} /> Attack contained. No action needed.</div>
        <p className="mt-1 text-sm text-rose-200/70">The message tried to instruct the system ({c.injection.patterns.length} injection pattern{c.injection.patterns.length > 1 ? "s" : ""}). It was stopped before any LLM or tool, and the policy gate refuses any payout.</p>
      </div>
    );
  if (c.outcome === "NO_REFUND_DUE")
    return (
      <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 text-sm text-slate-300">
        <div className="flex items-center gap-2 font-semibold text-slate-100"><CheckCircle2 size={18} /> No refund due</div>
        <p className="mt-1 text-slate-400">The bank ledger shows this payment reached {c.counterparty}. The customer was told, with the timestamp.</p>
      </div>
    );
  if (ap?.status === "PENDING") {
    const mine = ap.assigned_to === role;
    const left = Math.max(0, (ap.sla_hours ?? 12) - (ap.hours_waiting ?? 0));
    return (
      <div className="rounded-2xl border border-amber-500/25 bg-amber-500/[0.06] p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 font-semibold text-amber-100"><UserCheck size={18} /> Approval needed: refund {inr(ap.request.payload.amount)}</div>
            <p className="mt-1 max-w-2xl text-sm text-amber-100/70">{ap.reason_codes.map(reason).join(" · ")}</p>
          </div>
          <div className="text-right text-xs text-amber-100/70">
            {ap.escalated ? <span className="font-semibold text-rose-300">Escalated to {ap.assigned_to}</span> : <>With {ap.assigned_to} · {left.toFixed(1)}h left in SLA</>}
          </div>
        </div>
        <div className="mt-3 grid gap-2 rounded-xl bg-black/20 p-3 text-sm text-slate-300 sm:grid-cols-3">
          <div><div className="text-[11px] uppercase tracking-wide text-slate-500">Ledger</div>{c.verified.gateway_status.toLowerCase()} at gateway · {c.verified.bank_status.toLowerCase().replace(/_/g, " ")}</div>
          <div><div className="text-[11px] uppercase tracking-wide text-slate-500">RBI {c.rbi.tat_rule}</div>{c.rbi.is_tat_breached ? `Breached · ${inr(c.rbi.accrued_compensation_inr)} exposure` : `${c.rbi.days_until_breach}d left`}</div>
          <div><div className="text-[11px] uppercase tracking-wide text-slate-500">Closes on</div>{c.promise?.draft.proof_check_id.replace(/_/g, " ").toLowerCase()}</div>
        </div>
        <Brief text={ap.brief} live={c.crew?.mode === "CREWAI_SARVAM_LIVE"} />
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button disabled={!mine || busy} onClick={() => act(() => api.approve(c.txn_id, role), "Approved. Refund sent to bank; awaiting proof.")}
            className="rounded-xl bg-emerald-600 px-5 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40">Approve refund</button>
          <button disabled={!mine || busy} onClick={() => act(() => api.reject(c.txn_id, role), "Rejected. Moved to manual review.")}
            className="rounded-xl border border-white/15 px-5 py-2 text-sm font-medium text-slate-200 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40">Reject</button>
          {!mine && <span className="text-xs text-amber-200/80">Authority is with {ap.assigned_to}. Switch "Signed in as" at the top to act.</span>}
          <span className="ml-auto flex items-center gap-1 font-mono text-[10px] text-slate-500"><Fingerprint size={11} /> HMAC {ap.request.token.slice(0, 16)}…</span>
        </div>
      </div>
    );
  }
  if (c.state === "AWAITING_PROOF")
    return (
      <div className="rounded-2xl border border-sky-500/20 bg-sky-500/[0.05] p-5">
        <div className="flex items-center gap-2 font-semibold text-sky-100"><Lock size={18} /> Refund sent. Case stays open until the bank proves it.</div>
        <p className="mt-1 text-sm text-sky-100/70">The bank API accepted the refund (202), but acceptance isn't settlement. The independent verifier closes the case only on a bank-signed, settled 12-digit UTR.</p>
        <button onClick={() => ask("Bank settles this refund")} className="mt-3 text-xs text-sky-300 hover:underline">Sandbox: simulate the bank settling →</button>
      </div>
    );
  if (c.state === "VERIFIED_CLOSED") {
    const r = c.verification?.receipt;
    return (
      <div className="rounded-2xl border border-emerald-500/25 bg-emerald-500/[0.06] p-5">
        <div className="flex items-center gap-2 font-semibold text-emerald-100"><ShieldCheck size={18} /> Verified closed</div>
        <p className="mt-1 text-sm text-emerald-100/70">{inr(r?.amount)} credited · UTR <span className="font-mono">{r?.utr}</span> · {when(r?.settled_at)}. Customer received a Hindi voice receipt.</p>
      </div>
    );
  }
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 text-sm text-slate-300">
      <div className="flex items-center gap-2 font-semibold text-slate-100"><AlertTriangle size={18} /> {statusLabel(c)}</div>
      <p className="mt-1 text-slate-400">{(c.gate?.reason_codes ?? []).map(reason).join(" · ")}</p>
    </div>
  );
}

export default function CaseView({ c, role, refresh, toast, ask }: { c: Json | null; role: string; refresh: () => void; toast: (s: string) => void; ask: (q: string) => void }) {
  const [auditOpen, setAuditOpen] = useState(false);
  if (!c)
    return <div className="grid h-full place-items-center rounded-2xl border border-dashed border-white/10 text-sm text-slate-500">Select a case</div>;
  const p = c.promise?.draft, r = c.rbi, v = c.verified, claim = c.claimed;

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0 flex-1">
          <div className="text-xs text-slate-500">{c.txn_id} · {v.tx_type === "P2M" ? "Merchant payment" : "UPI transfer"} · opened {when(c.created_at)}</div>
          <h2 className="mt-1 text-2xl font-semibold text-white">{c.customer.name} <span className="text-slate-500">→</span> {c.counterparty.split(" (")[0]}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Pill tone={c.triage.tone}>{c.triage.label}</Pill>
            <span className="text-sm text-slate-400">{c.triage.text}</span>
            <button onClick={() => ask("Why was this case routed this way?")} className="flex items-center gap-0.5 text-xs text-emerald-300 hover:underline">Ask Copilot why <ArrowUpRight size={12} /></button>
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="font-mono text-3xl font-semibold text-white">{inr(v.verified_amount)}</div>
          <div className="text-xs text-slate-500">verified from bank ledger</div>
        </div>
      </div>

      <ActionBanner c={c} role={role} refresh={refresh} toast={toast} ask={ask} />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Panel title="What happened">
          <ol className="relative space-y-4">
            {c.chain.map((e: Json, i: number) => {
              const s = step(e, c);
              return (
                <li key={e.id} className="relative flex gap-3">
                  {i < c.chain.length - 1 && <span className="absolute left-[13px] top-7 h-[calc(100%-4px)] w-px bg-white/10" />}
                  <span className={`z-10 grid h-7 w-7 shrink-0 place-items-center rounded-full ${dot[s.tone]}`}>{s.icon}</span>
                  <div className="min-w-0 flex-1 pb-1">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-sm font-medium text-slate-100">{s.title}</span>
                      <span className="shrink-0 text-[11px] text-slate-500">{when(e.timestamp)}</span>
                    </div>
                    {s.body && <div className="mt-0.5 text-[13px] leading-relaxed text-slate-400">{s.body}</div>}
                  </div>
                </li>
              );
            })}
          </ol>
          <button onClick={() => setAuditOpen(!auditOpen)} className="mt-5 flex w-full items-center justify-between gap-2 rounded-xl bg-white/[0.03] px-3 py-2 text-left text-xs text-slate-400 hover:bg-white/[0.06]">
            <span className="flex items-center gap-2"><Fingerprint size={13} /> Tamper-evident audit trail · {c.chain.length} entries ·
              <span className={c.chain_valid ? "text-emerald-400" : "text-rose-400"}>{c.chain_valid ? "hash chain valid" : "chain broken"}</span></span>
            <ChevronDown size={14} className={auditOpen ? "rotate-180" : ""} />
          </button>
          {auditOpen && (
            <div className="mt-2 max-h-64 space-y-1 overflow-y-auto font-mono text-[10px] text-slate-500">
              {c.chain.map((e: Json) => (
                <div key={e.id} className="rounded-lg bg-black/20 px-2 py-1.5">
                  <span className="text-slate-300">#{e.seq} {e.event}</span> · {e.from_state} → {e.to_state}
                  <div className="break-all">prev {e.prev_hash.slice(0, 20)}… → <span className="text-emerald-500/80">{e.current_hash.slice(0, 20)}…</span></div>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <div className="space-y-5">
          {c.crew?.diagnosis && (
            <Panel title={<span className="flex items-center gap-2"><Users size={14} /> Specialist findings</span>}
              action={<Pill tone={c.crew.mode === "CREWAI_SARVAM_LIVE" ? "green" : "slate"}>{c.crew.mode === "CREWAI_SARVAM_LIVE" ? "CrewAI · Sarvam" : "Local fallback"}</Pill>}>
              <p className="text-sm leading-relaxed text-slate-300">{c.crew.diagnosis.root_cause}</p>
              {c.crew.validation_overrides?.length > 0 && (
                <div className="mt-3 rounded-xl bg-sky-500/[0.06] p-3">
                  <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-sky-300">Policy corrected the LLM draft</div>
                  {c.crew.validation_overrides.map((o: Json) => (
                    <div key={o.field} className="text-xs leading-relaxed text-slate-400"><span className="font-mono text-slate-300">{o.field}</span>: {o.reason}</div>
                  ))}
                </div>
              )}
            </Panel>
          )}
          {p && (
            <Panel title="Promise">
              <Field label="What">{p.what}</Field>
              <Field label="By when">{when(p.by_when)}</Field>
              <Field label="Owner">{p.who}</Field>
              <Field label="Closes only on">{p.proof_check_id.replace(/_/g, " ").toLowerCase()}</Field>
              {p.narrower_controllable_commitment && <div className="mt-2 rounded-xl bg-amber-500/[0.06] p-3 text-xs leading-relaxed text-amber-100/80">{p.narrower_controllable_commitment}</div>}
            </Panel>
          )}

          <Panel title={<span className="flex items-center gap-2"><Scale size={14} /> RBI turnaround</span>} action={<Pill tone={r.is_tat_breached ? "red" : "green"}>{r.is_tat_breached ? "Breached" : `${r.days_until_breach}d left`}</Pill>}>
            <div className="text-sm text-slate-300">{r.tat_rule} for {v.tx_type === "P2M" ? "merchant payments" : "P2P transfers"} · deadline {when(r.tat_deadline_iso)}</div>
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/5">
              <div className={`h-full ${r.is_tat_breached ? "bg-rose-400" : "bg-emerald-400"}`} style={{ width: `${Math.min(100, (c.breach.rbi_tat_progress ?? 0) * 100)}%` }} />
            </div>
            {r.accrued_compensation_inr > 0 && (
              <div className="mt-3 text-sm text-rose-200">{inr(r.accrued_compensation_inr)} compensation exposure <span className="text-rose-200/60">(₹{r.comp_rate_inr_per_day}/day)</span></div>
            )}
            <div className="mt-2 text-[11px] leading-relaxed text-slate-500">Payable by {r.liability_entity}, not VAADA. Circular {r.circular_ref.split(" ")[0]}.</div>
          </Panel>

          <Panel title="Customer said vs. bank records">
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="rounded-xl bg-white/[0.03] p-3">
                <div className="mb-1 font-semibold uppercase tracking-wide text-slate-500">Claim · untrusted</div>
                <div className="italic text-slate-300">“{(claim.raw_text || claim.voice_transcript).slice(0, 140)}”</div>
                {claim.voice_transcript && <div className="mt-1 text-slate-500">via voice</div>}
                {c.attachments?.length > 0 && <div className="mt-1 text-slate-500">+ {c.attachments.length} screenshot</div>}
              </div>
              <div className="rounded-xl bg-white/[0.03] p-3">
                <div className="mb-1 font-semibold uppercase tracking-wide text-slate-500">Ledger · trusted</div>
                <div className="space-y-0.5 text-slate-300">
                  <div>{inr(v.verified_amount)} · {when(v.txn_timestamp)}</div>
                  <div className="text-slate-500">gateway {v.gateway_status.toLowerCase()}</div>
                  <div className="text-slate-500">bank {v.bank_status.toLowerCase().replace(/_/g, " ")}</div>
                </div>
              </div>
            </div>
            {c.claim_vs_fact.mismatches.length > 0 && (
              <div className="mt-3 rounded-xl bg-rose-500/[0.07] p-2.5 text-xs text-rose-200">
                Mismatch: claimed {c.claim_vs_fact.mismatches.map((m: Json) => `${m.claimed} vs ledger ${m.ledger}`).join(", ")}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
