import { ReactNode, useState } from "react";
import { ArrowUpRight, ChevronDown } from "lucide-react";
import { api, clockTime, inr, Json, reason, when } from "./api";
import { statusLabel } from "./Inbox";
import { Button, Dot, Panel, Pill } from "./ui";

/* ------------------------------------------------------------------ timeline copy */
type Step = { title: ReactNode; body?: ReactNode; tone: string };

function step(e: Json): Step {
  const d = JSON.parse(e.payload).data ?? {};
  const codes = (x: string[] = []) => x.map(reason).join(" · ");
  const mono = (s: string) => <span className="num text-ink">{s}</span>;
  switch (e.event) {
    case "OPEN_TRIAGED":
      return { tone: "blue", title: "Case opened from customer chat",
        body: <><span className="font-serif text-[15px] italic text-ink">“{d.customer_text}”</span><div className="mt-1">{d.nirnay_route === "fast_path" ? "Nirnay triaged it locally with high confidence." : "Nirnay flagged it for review."} <span className="num text-ink-3">{d.latency_ms} ms</span></div></> };
    case "SPECIALISTS_ENGAGED":
      return { tone: "amber", title: "Specialist squad engaged", body: `Three CrewAI agents on Sarvam, bounded to ${d.timeout_s}s. Customer told to expect an update.` };
    case "SPECIALISTS_REPORTED":
      return { tone: d.mode === "CREWAI_SARVAM_LIVE" ? "green" : "slate",
        title: d.mode === "CREWAI_SARVAM_LIVE" ? "Specialist squad reported" : "Specialist review completed (local fallback)",
        body: d.overrides?.length ? `Policy corrected ${d.overrides.length} field${d.overrides.length > 1 ? "s" : ""} of the LLM draft: ${d.overrides.join(", ")}.` : d.error ? `Live squad unavailable: ${String(d.error).slice(0, 90)}` : undefined };
    case "CLAIM_VERIFIED":
      return { tone: ({ VERIFIED: "green", PARTIAL: "green", IN_FLIGHT: "green", CONTRADICTED: "red" } as Record<string, string>)[d.verdict] ?? "slate",
        title: `Claim checked against records — ${String(d.verdict).replace(/_/g, " ").toLowerCase()}`,
        body: <>Sources: {(d.sources ?? []).map((x: string) => x.replace(/_/g, " ").toLowerCase()).join(", ")}{d.amount_due ? <> · {mono(inr(d.amount_due))} owed under policy</> : null}</> };
    case "PROMISE_COMPILED":
      return { tone: "green", title: "Promise made to the customer", body: <>{d.what} · by {when(d.by_when)}</> };
    case "POLICY_HOLD":
      return { tone: "amber", title: "Held for human approval", body: codes(d.reason_codes) };
    case "APPROVER_ESCALATED":
      return { tone: "red", title: "Authority escalated", body: <>{mono(d.from)} didn't act within SLA; moved to {mono(d.to)}.</> };
    case "PROACTIVE_CUSTOMER_UPDATE":
      return { tone: "blue", title: "Customer updated before our own deadline" };
    case "REFUND_API_ACCEPTED_202":
      return { tone: "green", title: <>Refund of {inr(d.amount)} sent to bank</>, body: "Bank API answered 202 Accepted. That is not settlement." };
    case "MERCHANT_REFUND_REQUESTED_202":
      return { tone: "green", title: `Refund of ${inr(d.amount)} requested from merchant`, body: "Merchant API answered 202 Accepted. Not credited yet." };
    case "MERCHANT_REFUND_IN_FLIGHT":
      return { tone: "green", title: `Merchant already refunding ${inr(d.amount)}`, body: <>Tracking existing refund {mono(d.refund_ref)}; no duplicate request.</> };
    case "AWAITING_SETTLED_UTR":
    case "AWAITING_REFUND_CREDIT":
      return { tone: "slate", title: "Waiting for proof", body: "Closes only on a bank-signed, settled UTR." };
    case "DUPLICATE_RETRY_ATTEMPT":
      return d.reason_codes?.includes("DUPLICATE_ACTION_BLOCKED")
        ? { tone: "amber", title: "Duplicate refund attempt blocked", body: "SHA-256 idempotency lock prevented a second payout." }
        : { tone: "slate", title: "Retry not executed", body: codes(d.reason_codes) };
    case "BANK_WEBHOOK_RECEIVED":
      return { tone: "green", title: "Credit confirmed by the bank", body: <>UTR {mono(d.utr)}</> };
    case "PROOF_VERIFIED":
      return { tone: "green", title: "Verified and closed", body: "Independent auditor checked the signed receipt over a read-only connection." };
    case "ADVERSARIAL_INPUT_CONTAINED":
      return { tone: "red", title: "Prompt injection contained", body: "The text never reached an LLM. No payout possible." };
    case "LEDGER_SHOWS_SUCCESS":
      return { tone: "slate", title: "Ledger shows the payment succeeded", body: "Customer told no refund is due." };
    case "POLICY_BLOCK":
      return { tone: "red", title: "Blocked by the policy gate", body: codes(d.reason_codes) };
    case "MERCHANT_DISPUTE_RAISED":
      return { tone: "amber", title: <>Dispute raised with merchant</>, body: <>{mono(d.ref)} · no order data, so no money promised. Answer due {when(d.respond_by)}.</> };
    case "MERCHANT_RECORD_NO_REFUND_DUE":
      return { tone: "slate", title: "Merchant record shows nothing owed", body: `Order ${String(d.status).toLowerCase().replace(/_/g, " ")}.` };
    case "ALREADY_REFUNDED":
      return { tone: "slate", title: `Already refunded ${inr(d.amount)}`, body: <>Credited {when(d.credited_at)} · UTR {mono(d.utr)}</> };
    case "ORDER_CLAIMED_BY_OTHER_ACCOUNT":
      return { tone: "red", title: "Another account tried to claim this order", body: <>Flagged to security ({mono(d.claimed_in_case)}). This customer's refund is unaffected.</> };
    case "APPROVAL_REJECTED":
      return { tone: "red", title: `Rejected by ${d.by}`, body: "Moved to manual review." };
    default:
      return { tone: "slate", title: e.event.replace(/_/g, " ").toLowerCase() };
  }
}

const TONE_DOT: Record<string, string> = { green: "bg-ok", amber: "bg-warn", red: "bg-bad", blue: "bg-info", slate: "bg-ink-4" };

/* ------------------------------------------------------------------ lifecycle track */
const STAGES: [string, string][] = [
  ["OPEN_TRIAGED", "Triaged"], ["PROMISE_ACTIVE", "Promised"], ["AWAITING_APPROVAL", "Approval"],
  ["ACTION_EXECUTED", "Executed"], ["AWAITING_PROOF", "Proof"], ["VERIFIED_CLOSED", "Closed"],
];

function Lifecycle({ c }: { c: Json }) {
  const reached = new Set<string>((c.chain ?? []).map((e: Json) => e.to_state));
  const at = STAGES.findIndex(([s]) => s === c.state);
  const stopped = !!c.outcome;
  return (
    <div className="flex items-center gap-1">
      {STAGES.map(([s, label], i) => {
        const done = reached.has(s) || i < at;
        const cur = i === at;
        const tone = stopped && cur ? (c.outcome === "CONTAINED" || c.outcome === "CLAIM_CONTRADICTED" ? "bg-bad" : "bg-ink-3")
          : s === "VERIFIED_CLOSED" && cur ? "bg-ok" : cur ? "bg-sindoor" : done ? "bg-ink" : "bg-line-strong";
        return (
          <div key={s} className="flex min-w-0 flex-1 flex-col gap-1.5">
            <span className={`h-[3px] rounded-full ${tone}`} />
            <span className={`eyebrow truncate !text-[9.5px] ${cur ? "!text-ink" : done ? "!text-ink-2" : ""}`}>{label}</span>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ action banner */
function Banner({ tone, title, children, actions }: { tone: string; title: ReactNode; children?: ReactNode; actions?: ReactNode }) {
  const rule = { green: "border-l-ok", amber: "border-l-warn", red: "border-l-bad", blue: "border-l-info", slate: "border-l-ink-3", accent: "border-l-sindoor" }[tone];
  return (
    <div className={`rise rounded-lg border border-line border-l-[3px] ${rule} bg-card px-5 py-4`}>
      <div className="text-[15px] font-medium leading-snug text-ink">{title}</div>
      {children && <div className="mt-1.5 text-[13px] leading-relaxed text-ink-2">{children}</div>}
      {actions && <div className="mt-4 flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

function ActionBanner({ c, role, refresh, toast, ask }: { c: Json; role: string; refresh: () => void; toast: (s: string) => void; ask: (q: string) => void }) {
  const ap = c.approval;
  const o = c.evidence?.order;
  const [busy, setBusy] = useState(false);
  const [brief, setBrief] = useState(false);
  const act = async (fn: () => Promise<Json>, ok: string) => {
    setBusy(true);
    try { await fn(); toast(ok); } catch (e) { toast((e as Error).message); }
    setBusy(false); refresh();
  };

  if (c.crew?.mode === "RUNNING")
    return (
      <Banner tone="amber" title={<span className="flex items-center gap-2"><Dot tone="amber" pulse /> Specialist squad is investigating</span>}>
        Investigator, promise compiler and planner are reconciling gateway, bank and merchant records on Sarvam. Bounded to {c.crew.timeout_s}s, then the deterministic squad takes over. The customer already has an acknowledgement.
      </Banner>
    );
  if (c.outcome === "CONTAINED")
    return <Banner tone="red" title="Attack contained. Nothing to do.">The message tried to instruct the system ({c.injection.patterns.length} injection pattern{c.injection.patterns.length > 1 ? "s" : ""}). It was stopped before any LLM or tool, and the policy gate refuses any payout.</Banner>;
  if (c.outcome === "CLAIM_CONTRADICTED") {
    const fails = (c.evidence?.checks ?? []).filter((x: Json) => x.status === "FAIL");
    return <Banner tone="red" title="The records contradict this claim. No refund.">{fails[0] ? `${fails[0].label}: ${fails[0].detail}.` : ""} {fails.length > 1 ? `${fails.length - 1} more check${fails.length > 2 ? "s" : ""} failed below.` : ""} The customer was told why, and nothing was promised.</Banner>;
  }
  if (c.outcome === "NO_REFUND_DUE")
    return <Banner tone="slate" title="No refund due">{o ? `${o.merchant_name}'s record shows ${o.order_label} ${o.order_ref} as ${o.status.toLowerCase().replace(/_/g, " ")}; the ${o.category.toLowerCase()} policy owes nothing.` : `The bank ledger shows this payment reached ${c.counterparty}.`} The customer was told why.</Banner>;
  if (c.outcome === "ALREADY_REFUNDED") {
    const r = o?.refunds?.find((x: Json) => x.status === "CREDITED");
    return <Banner tone="slate" title="Already refunded. Second refund refused."><span className="num text-ink">{inr(r?.amount)}</span> was credited {when(r?.credited_at)} (UTR <span className="num text-ink">{r?.utr}</span>). The merchant record shows nothing more is owed.</Banner>;
  }
  if (c.outcome === "DISPUTE_RAISED")
    return <Banner tone="amber" title={<>Can't verify yet — dispute <span className="num">{c.dispute.ref}</span> raised</>}>Payment confirmed, but there's no connector to {c.counterparty}'s order system, so a refund can't be confirmed. Vaada promised the merchant's answer by {when(c.dispute.respond_by)}, not money.</Banner>;
  if (c.outcome === "MANUAL_REVIEW" || c.outcome === "REJECTED")
    return <Banner tone="amber" title="Manual review">{(c.gate?.reason_codes ?? []).map(reason).join(" · ") || "A specialist will follow up within 24 hours."}</Banner>;

  if (ap?.status === "PENDING") {
    const mine = ap.assigned_to === role;
    const left = Math.max(0, (ap.sla_hours ?? 12) - (ap.hours_waiting ?? 0));
    return (
      <Banner tone="accent"
        title={<div className="flex flex-wrap items-baseline justify-between gap-3">
          <span>Approval needed — {o ? `ask ${o.merchant_name} to refund ` : "refund "}<span className="num">{inr(ap.request.payload.amount)}</span></span>
          <span className="text-[12.5px] font-normal text-ink-3">{ap.escalated ? <span className="font-medium text-bad">Escalated to {ap.assigned_to}</span> : <>With <span className="num text-ink-2">{ap.assigned_to}</span> · <span className="num">{left.toFixed(1)}h</span> left in SLA</>}</span>
        </div>}
        actions={<>
          <Button variant="primary" disabled={!mine || busy} onClick={() => act(() => api.approve(c.txn_id, role), "Approved. Refund requested; awaiting proof.")}>Approve refund</Button>
          <Button disabled={!mine || busy} onClick={() => act(() => api.reject(c.txn_id, role), "Rejected. Moved to manual review.")}>Reject</Button>
          <Button variant="ghost" onClick={() => setBrief(!brief)}>{c.crew?.mode === "CREWAI_SARVAM_LIVE" ? "Squad's brief" : "Brief"} <ChevronDown size={13} className={brief ? "rotate-180" : ""} /></Button>
          {!mine && <span className="text-[12px] text-warn">Authority is with {ap.assigned_to}. Switch “Signed in as” to act.</span>}
          <span className="num ml-auto text-[10.5px] text-ink-4">HMAC {ap.request.token.slice(0, 16)}…</span>
        </>}>
        <div>{ap.reason_codes.map(reason).join(" · ")}</div>
        <div className="mt-3 grid divide-x divide-line rounded-md border border-line sm:grid-cols-3">
          <div className="px-3 py-2"><div className="eyebrow">{o ? "Merchant record" : "Ledger"}</div><div className="mt-0.5 text-[12.5px] text-ink">{o ? `${o.order_label} ${o.order_ref} · ${o.status.toLowerCase().replace(/_/g, " ")}` : `${c.verified.gateway_status.toLowerCase()} · ${c.verified.bank_status.toLowerCase().replace(/_/g, " ")}`}</div></div>
          <div className="px-3 py-2"><div className="eyebrow">{c.rbi ? `RBI ${c.rbi.tat_rule}` : "Refund deadline"}</div><div className="mt-0.5 text-[12.5px] text-ink">{c.rbi ? (c.rbi.is_tat_breached ? <span className="text-bad">Breached · {inr(c.rbi.accrued_compensation_inr)} exposure</span> : `${c.rbi.days_until_breach}d left`) : when(c.sla?.deadline_iso)}</div></div>
          <div className="px-3 py-2"><div className="eyebrow">Closes on</div><div className="mt-0.5 text-[12.5px] text-ink">{c.promise?.draft.proof_check_id.replace(/_/g, " ").toLowerCase()}</div></div>
        </div>
        {brief && <p className="mt-3 rounded-md bg-sunk px-3 py-2.5 text-[12.5px] leading-relaxed text-ink-2">{ap.brief}</p>}
      </Banner>
    );
  }
  if (c.state === "AWAITING_PROOF")
    return (
      <Banner tone="blue" title={o ? <>{o.merchant_name} is refunding <span className="num">{inr(c.expected_refund)}</span>. Open until it's credited.</> : "Refund sent. Open until the bank proves it."}
        actions={<Button size="sm" onClick={() => ask("Bank settles this refund")}>Sandbox: credit the refund</Button>}>
        {o ? "Acceptance isn't money in the account. The independent verifier closes the case only on a bank-signed, settled UTR linked to this payment, for exactly the amount the policy owes."
          : "The bank API accepted the refund (202), but acceptance isn't settlement. The independent verifier closes the case only on a bank-signed, settled 12-digit UTR."}
      </Banner>
    );
  if (c.state === "VERIFIED_CLOSED") {
    const r = c.verification?.receipt;
    return <Banner tone="green" title="Verified and closed"><span className="num text-ink">{inr(r?.amount)}</span> credited · UTR <span className="num text-ink">{r?.utr}</span> · {when(r?.settled_at)}. The customer received a Hindi voice receipt.</Banner>;
  }
  return <Banner tone="slate" title={statusLabel(c)}>{(c.gate?.reason_codes ?? []).map(reason).join(" · ")}</Banner>;
}

/* ------------------------------------------------------------------ promise contract */
function PromiseContract({ c }: { c: Json }) {
  const p = c.promise?.draft;
  if (!p) return null;
  const cell = (k: string, v: ReactNode) => (
    <div className="min-w-0 px-4 py-3">
      <div className="eyebrow">{k}</div>
      <div className="mt-1 text-[13px] leading-snug text-ink">{v}</div>
    </div>
  );
  return (
    <section className="rise overflow-hidden rounded-lg border border-line bg-card">
      <div className="flex items-baseline justify-between border-b border-dashed border-line-strong px-4 py-2.5">
        <span className="font-serif text-[18px] leading-none text-ink">Promise contract</span>
        <span className="eyebrow">{c.state === "VERIFIED_CLOSED" ? <span className="text-ok">Kept · proven</span> : "Active · tracked"}</span>
      </div>
      <div className="grid divide-line sm:grid-cols-2 sm:divide-x xl:grid-cols-[1.5fr_1fr_1fr_1fr]">
        {cell("What", p.what)}
        {cell("By when", <span className="num">{when(p.by_when)}</span>)}
        {cell("Who", p.who)}
        {cell("Proof", <span className="num text-[12px]">{p.proof_check_id}</span>)}
      </div>
      {p.narrower_controllable_commitment && <div className="border-t border-line bg-[#faf9f6] px-4 py-2.5 text-[12.5px] leading-relaxed text-ink-2">{p.narrower_controllable_commitment}</div>}
    </section>
  );
}

/* ------------------------------------------------------------------ evidence */
const GLYPH: Record<string, [string, string]> = { PASS: ["✓", "text-ok bg-ok-soft"], FAIL: ["✕", "text-bad bg-bad-soft"], WARN: ["!", "text-warn bg-warn-soft"] };
const VERDICT: Record<string, [string, string]> = {
  VERIFIED: ["Verified", "green"], PARTIAL: ["Partly refundable", "green"], IN_FLIGHT: ["Verified · merchant refunding", "green"],
  CONTRADICTED: ["Contradicted", "red"], UNVERIFIABLE: ["Can't verify", "amber"], NOT_DUE: ["Nothing owed", "slate"],
  ALREADY_REFUNDED: ["Already refunded", "slate"], NEEDS_REVIEW: ["Needs review", "amber"],
};
const TRUST: Record<string, string> = { HIGH: "high trust", POLICY: "policy", LOW: "low trust", UNTRUSTED: "untrusted" };

function Evidence({ ev }: { ev: Json }) {
  const [v, tone] = VERDICT[ev.verdict] ?? [ev.verdict, "slate"];
  const n = (st: string) => ev.checks.filter((x: Json) => x.status === st).length;
  const order = { FAIL: 0, WARN: 1, PASS: 2 } as Record<string, number>;
  const checks = [...ev.checks].sort((a: Json, b: Json) => order[a.status] - order[b.status]);
  return (
    <Panel flush title="How the claim was verified"
      action={<div className="flex items-center gap-3">
        <span className="num hidden text-[11.5px] text-ink-3 sm:inline">{n("PASS")} pass · {n("FAIL")} fail · {n("WARN")} warn</span>
        <Pill tone={tone}>{v}</Pill></div>}>
      <ul>
        {checks.map((x: Json, i: number) => {
          const [g, gc] = GLYPH[x.status] ?? ["·", "text-ink-3 bg-sunk"];
          return (
            <li key={i} className={`grid grid-cols-[22px_minmax(0,1fr)] gap-3 px-4 py-3 sm:grid-cols-[22px_minmax(0,1fr)_150px] ${i ? "border-t border-line" : ""}`}>
              <span className={`num mt-px grid h-[20px] w-[20px] place-items-center rounded-[4px] text-[11px] font-semibold ${gc}`}>{g}</span>
              <div className="min-w-0">
                <div className="text-[13px] text-ink">{x.label}</div>
                <div className="mt-0.5 text-[12.5px] leading-relaxed text-ink-2">{x.detail}</div>
              </div>
              <div className="col-start-2 sm:col-start-auto sm:text-right">
                <div className="text-[12px] text-ink-2">{x.source_label}</div>
                <div className={`eyebrow !text-[9.5px] ${x.trust === "HIGH" ? "!text-ok" : x.trust === "POLICY" ? "!text-info" : ""}`}>{TRUST[x.trust]}</div>
              </div>
            </li>
          );
        })}
      </ul>
      <div className="border-t border-line bg-[#faf9f6] px-4 py-2.5 text-[12px] leading-relaxed text-ink-3">
        Only bank, merchant and history records can make a claim pass. The customer's words and screenshots can only make it fail.
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ page */
export default function CaseView({ c, role, refresh, toast, ask }: { c: Json | null; role: string; refresh: () => void; toast: (s: string) => void; ask: (q: string) => void }) {
  const [auditOpen, setAuditOpen] = useState(false);
  if (!c)
    return (
      <div className="grid min-h-[420px] place-items-center rounded-lg border border-dashed border-line-strong">
        <div className="text-center">
          <div className="font-serif text-[26px] text-ink">No case selected</div>
          <p className="mt-1 text-[13px] text-ink-3">Cases appear here as customers write in.</p>
        </div>
      </div>
    );
  const r = c.rbi, v = c.verified, claim = c.claimed, o = c.evidence?.order;
  const day = (iso: string) => new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

  return (
    <div className="min-w-0 space-y-5">
      {/* header */}
      <div className="rise">
        <div className="eyebrow flex flex-wrap gap-x-2">
          <span className="text-ink-2">{c.txn_id}</span><span>·</span>
          <span>{c.kind === "MERCHANT_REFUND" ? "Refund after a successful payment" : v.tx_type === "P2M" ? "Failed merchant payment" : "Failed UPI transfer"}</span>
          {o && <><span>·</span><span>{o.order_label} {o.order_ref}</span></>}
          <span>·</span><span>Opened {when(c.created_at)}</span>
        </div>
        <div className="mt-2 flex items-start justify-between gap-6">
          <div className="min-w-0">
            <h1 className="font-serif text-[40px] leading-[1.05] tracking-[-0.01em] text-ink">
              {c.customer.name} <span className="text-ink-4">→</span> {c.counterparty.split(" (")[0]}
            </h1>
            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
              <Pill tone={c.triage.tone}>{c.triage.label}</Pill>
              <span className="text-[13px] text-ink-2">{c.triage.text}</span>
              <button onClick={() => ask("Why was this case routed this way?")} className="inline-flex items-center gap-0.5 text-[12.5px] text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">Ask copilot why<ArrowUpRight size={12} /></button>
            </div>
          </div>
          <div className="shrink-0 text-right">
            <div className="num text-[34px] font-medium leading-none tracking-[-0.03em] text-ink">{inr(v.verified_amount)}</div>
            <div className="eyebrow mt-2">{c.kind === "MERCHANT_REFUND" ? "Paid · bank ledger" : "Verified · bank ledger"}</div>
            {c.kind === "MERCHANT_REFUND" && c.evidence?.amount_due != null && c.evidence.amount_due !== v.verified_amount &&
              <div className={`num mt-1 text-[12px] ${c.evidence.amount_due > 0 ? "text-ok" : "text-ink-3"}`}>{c.evidence.amount_due > 0 ? `${inr(c.evidence.amount_due)} owed` : "nothing owed"}</div>}
          </div>
        </div>
        <div className="mt-5"><Lifecycle c={c} /></div>
      </div>

      <ActionBanner c={c} role={role} refresh={refresh} toast={toast} ask={ask} />
      <PromiseContract c={c} />
      {c.evidence && <Evidence ev={c.evidence} />}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_330px]">
        {/* timeline */}
        <Panel flush title="Timeline" action={<span className="eyebrow">Hash-chained</span>}>
          <ol className="px-4 py-3">
            {c.chain.map((e: Json, i: number) => {
              const s = step(e);
              const last = i === c.chain.length - 1;
              return (
                <li key={e.id} className="grid grid-cols-[62px_14px_minmax(0,1fr)] gap-x-3">
                  <div className="pt-[1px] text-right">
                    <div className="num whitespace-nowrap text-[11.5px] text-ink-2">{clockTime(e.timestamp)}</div>
                    <div className="num text-[10px] text-ink-4">{day(e.timestamp)}</div>
                  </div>
                  <div className="relative flex justify-center">
                    <span className={`relative z-10 mt-[5px] h-[9px] w-[9px] rounded-full ring-[3px] ring-card ${TONE_DOT[s.tone]}`} />
                    {!last && <span className="absolute bottom-0 top-[14px] w-px bg-line" />}
                  </div>
                  <div className={`min-w-0 ${last ? "pb-1" : "pb-5"}`}>
                    <div className="text-[13px] font-medium text-ink">{s.title}</div>
                    {s.body && <div className="mt-0.5 text-[12.5px] leading-relaxed text-ink-2">{s.body}</div>}
                  </div>
                </li>
              );
            })}
          </ol>
          <button onClick={() => setAuditOpen(!auditOpen)} className="flex w-full items-center justify-between gap-2 border-t border-line bg-[#faf9f6] px-4 py-2.5 text-left hover:bg-sunk">
            <span className="eyebrow flex items-center gap-2">Audit trail · {c.chain.length} entries · <span className={c.chain_valid ? "!text-ok" : "!text-bad"}>{c.chain_valid ? "chain valid" : "chain broken"}</span></span>
            <ChevronDown size={14} className={`text-ink-3 ${auditOpen ? "rotate-180" : ""}`} />
          </button>
          {auditOpen && (
            <div className="max-h-64 space-y-px overflow-y-auto border-t border-line bg-[#faf9f6] px-4 py-2 font-mono text-[10.5px] text-ink-3">
              {c.chain.map((e: Json) => (
                <div key={e.id} className="py-1">
                  <span className="text-ink">#{e.seq} {e.event}</span> · {e.from_state} → {e.to_state}
                  <div className="break-all">{e.prev_hash.slice(0, 18)}… → <span className="text-ok">{e.current_hash.slice(0, 18)}…</span></div>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <div className="space-y-5">
          {c.crew?.diagnosis && (
            <Panel title="Specialist findings" action={<Pill tone={c.crew.mode === "CREWAI_SARVAM_LIVE" ? "accent" : "slate"}>{c.crew.mode === "CREWAI_SARVAM_LIVE" ? "CrewAI · Sarvam" : "Local fallback"}</Pill>}>
              <p className="text-[13px] leading-relaxed text-ink-2">{c.crew.diagnosis.root_cause}</p>
              {c.crew.validation_overrides?.length > 0 && (
                <div className="mt-3 border-t border-dashed border-line-strong pt-3">
                  <div className="eyebrow mb-1.5 !text-info">Policy corrected the LLM</div>
                  {c.crew.validation_overrides.map((x: Json) => (
                    <div key={x.field} className="py-0.5 text-[12px] leading-relaxed text-ink-2"><span className="num text-ink">{x.field}</span> — {x.reason}</div>
                  ))}
                </div>
              )}
            </Panel>
          )}

          {r ? (
            <Panel title="RBI turnaround" action={<Pill tone={r.is_tat_breached ? "red" : "green"}>{r.is_tat_breached ? "Breached" : `${r.days_until_breach}d left`}</Pill>}>
              <div className="text-[13px] text-ink-2"><span className="num text-ink">{r.tat_rule}</span> for {v.tx_type === "P2M" ? "merchant payments" : "P2P transfers"} · due {when(r.tat_deadline_iso)}</div>
              <div className="mt-3 h-[3px] overflow-hidden rounded-full bg-sunk"><div className={`h-full ${r.is_tat_breached ? "bg-bad" : "bg-ink"}`} style={{ width: `${Math.min(100, (c.breach.rbi_tat_progress ?? 0) * 100)}%` }} /></div>
              {r.accrued_compensation_inr > 0 && <div className="mt-3 text-[13px] text-bad"><span className="num">{inr(r.accrued_compensation_inr)}</span> compensation exposure <span className="text-ink-3">(₹{r.comp_rate_inr_per_day}/day)</span></div>}
              <div className="mt-2 text-[11.5px] leading-relaxed text-ink-3">Payable by {r.liability_entity}, not Vaada. Circular {r.circular_ref.split(" ")[0]}.</div>
            </Panel>
          ) : c.sla?.deadline_iso ? (
            <Panel title="Refund deadline" action={<Pill tone={c.sla.is_breached ? "red" : "green"}>{c.sla.is_breached ? "Breached" : c.sla.hours_left > 48 ? `${Math.floor(c.sla.hours_left / 24)}d left` : `${Math.max(0, Math.round(c.sla.hours_left))}h left`}</Pill>}>
              <div className="text-[13px] text-ink-2">{c.sla.source} · due <span className="num text-ink">{when(c.sla.deadline_iso)}</span></div>
              <div className="mt-3 h-[3px] overflow-hidden rounded-full bg-sunk"><div className={`h-full ${c.sla.is_breached ? "bg-bad" : "bg-ink"}`} style={{ width: `${Math.min(100, (c.sla.progress ?? 0) * 100)}%` }} /></div>
              <div className="mt-2 text-[11.5px] leading-relaxed text-ink-3">The payment succeeded, so RBI's failed-transaction TAT doesn't apply. The clock is the merchant's own refund policy.</div>
            </Panel>
          ) : null}

          <Panel flush title="Said vs. recorded">
            <div className="px-4 py-3">
              <div className="eyebrow">Customer said · untrusted</div>
              <p className="mt-1 font-serif text-[16px] italic leading-snug text-ink">“{(claim.raw_text || claim.voice_transcript).slice(0, 160)}”</p>
              {(claim.voice_transcript || c.attachments?.length > 0) && <div className="mt-1 text-[11.5px] text-ink-3">{claim.voice_transcript ? "via voice" : ""}{c.attachments?.length ? ` + ${c.attachments.length} screenshot` : ""}</div>}
            </div>
            <div className="border-t border-line px-4 py-3">
              <div className="eyebrow">Bank ledger · trusted</div>
              <div className="mt-1 text-[12.5px] text-ink"><span className="num">{inr(v.verified_amount)}</span> · {when(v.txn_timestamp)}</div>
              <div className="text-[12px] text-ink-3">gateway {v.gateway_status.toLowerCase()} · bank {v.bank_status.toLowerCase().replace(/_/g, " ")}</div>
            </div>
            {o && (
              <div className="border-t border-line px-4 py-3">
                <div className="eyebrow">Merchant record · trusted</div>
                <div className="mt-1 text-[12.5px] text-ink">{o.order_label} <span className="num">{o.order_ref}</span> · {o.merchant_name}</div>
                <div className="text-[12px] text-ink-3">{o.description}</div>
                <div className="text-[12px] text-ink-3">{o.status.toLowerCase().replace(/_/g, " ")}{o.cancelled_by ? ` by ${o.cancelled_by.toLowerCase()}` : ""} · {when(o.status_at)}</div>
              </div>
            )}
            {c.claim_vs_fact.mismatches.length > 0 && (
              <div className="border-t border-line bg-bad-soft px-4 py-2.5 text-[12px] text-bad">
                Mismatch: {c.claim_vs_fact.mismatches.map((m: Json) => `claimed ${m.claimed} vs ledger ${m.ledger}`).join(", ")}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
