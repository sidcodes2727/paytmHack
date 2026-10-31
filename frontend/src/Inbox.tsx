import { inr, Json } from "./api";
import { Dot } from "./ui";

const GROUPS: [string, string][] = [
  ["approval", "Needs approval"], ["in_progress", "In progress"], ["manual_review", "Manual review"],
  ["blocked", "Blocked"], ["closed", "Resolved"],
];

export const STATUS: Record<string, string> = {
  AWAITING_APPROVAL: "Waiting for approval", AWAITING_PROOF: "Refund in progress · awaiting proof",
  VERIFIED_CLOSED: "Closed · verified", PROMISE_ACTIVE: "Promise active", OPEN_TRIAGED: "Triaged",
};

export function statusLabel(c: Json) {
  if (c.crew_running || c.crew?.mode === "RUNNING") return "Specialists reviewing…";
  if (c.outcome === "CONTAINED") return "Blocked · security";
  if (c.outcome === "NO_REFUND_DUE") return "Resolved · no refund due";
  if (c.outcome === "REJECTED") return "Manual review";
  if (c.outcome === "CLAIM_CONTRADICTED") return "Blocked · claim contradicted";
  if (c.outcome === "ALREADY_REFUNDED") return "Resolved · already refunded";
  if (c.outcome === "DISPUTE_RAISED") return "Dispute raised with merchant";
  if (c.outcome === "MANUAL_REVIEW") return "Manual review";
  if (c.group === "manual_review") return "Manual review";
  return STATUS[c.state] ?? c.state;
}

const GROUP_TONE: Record<string, string> = { approval: "amber", in_progress: "blue", manual_review: "amber", blocked: "red", closed: "slate" };

function riskTone(r: number) {
  return r >= 75 ? "red" : r >= 40 ? "amber" : "green";
}

export default function Inbox({ cases, sel, onSelect }: { cases: Json[]; sel: string | null; onSelect: (id: string) => void }) {
  if (!cases.length)
    return (
      <div className="rounded-lg border border-dashed border-line-strong px-5 py-10 text-center">
        <div className="font-serif text-[20px] text-ink">Nothing yet.</div>
        <p className="mx-auto mt-2 max-w-[200px] text-[12.5px] leading-relaxed text-ink-3">Send a message from the customer's phone. Vaada finds the payment and opens a case here.</p>
      </div>
    );
  return (
    <div className="space-y-5">
      {GROUPS.map(([g, label]) => {
        const items = cases.filter((c) => c.group === g);
        if (!items.length) return null;
        return (
          <div key={g}>
            <div className="mb-1.5 flex items-center gap-2 px-1">
              <Dot tone={GROUP_TONE[g]} /><span className="eyebrow">{label}</span><span className="num ml-auto text-[11px] text-ink-3">{items.length}</span>
            </div>
            <div className="overflow-hidden rounded-lg border border-line bg-card">
              {items.map((c, i) => {
                const active = sel === c.txn_id;
                return (
                  <button key={c.txn_id} onClick={() => onSelect(c.txn_id)}
                    className={`relative block w-full px-3.5 py-3 text-left transition-colors ${i ? "border-t border-line" : ""} ${active ? "bg-sunk" : "hover:bg-[#faf9f6]"}`}>
                    {active && <span className="absolute inset-y-0 left-0 w-[3px] bg-sindoor" />}
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate text-[13.5px] font-medium text-ink">{c.customer}</span>
                      <span className="num text-[13px] text-ink">{inr(c.amount)}</span>
                    </div>
                    <div className="mt-0.5 truncate text-[12px] text-ink-3">
                      {c.kind === "MERCHANT_REFUND" ? "Merchant refund" : "Failed payment"} · {c.counterparty.split(" (")[0]}
                    </div>
                    <div className="mt-1.5 flex items-center gap-1.5 text-[12px] text-ink-2">
                      {g !== "closed" && g !== "blocked" ? <Dot tone={riskTone(c.risk)} pulse={c.crew_running} /> : null}
                      <span className="truncate">{statusLabel(c)}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
