import { Inbox as InboxIcon } from "lucide-react";
import { inr, Json } from "./api";

const GROUPS: [string, string][] = [
  ["approval", "Needs approval"], ["in_progress", "In progress"], ["manual_review", "Manual review"],
  ["blocked", "Blocked"], ["closed", "Closed"],
];

export const STATUS: Record<string, string> = {
  AWAITING_APPROVAL: "Waiting for approval", AWAITING_PROOF: "Refund sent · awaiting bank proof",
  VERIFIED_CLOSED: "Closed · verified", PROMISE_ACTIVE: "Promise active", OPEN_TRIAGED: "Triaged",
};

export function statusLabel(c: Json) {
  if (c.crew_running || c.crew?.mode === "RUNNING") return "Specialists reviewing…";
  if (c.outcome === "CONTAINED") return "Blocked · security";
  if (c.outcome === "NO_REFUND_DUE") return "Resolved · no refund due";
  if (c.outcome === "REJECTED") return "Manual review";
  if (c.group === "manual_review") return "Manual review";
  return STATUS[c.state] ?? c.state;
}

function riskTone(r: number) {
  return r >= 75 ? "bg-rose-400" : r >= 40 ? "bg-amber-400" : "bg-emerald-400";
}

export default function Inbox({ cases, sel, onSelect }: { cases: Json[]; sel: string | null; onSelect: (id: string) => void }) {
  if (!cases.length)
    return (
      <div className="flex h-full flex-col items-center justify-center rounded-2xl border border-dashed border-white/10 p-6 text-center">
        <InboxIcon className="mb-3 text-slate-600" />
        <div className="text-sm font-medium text-slate-300">No cases yet</div>
        <div className="mt-1 text-xs text-slate-500">Send a message from the customer phone. VAADA will find the transaction and open a case here.</div>
      </div>
    );
  return (
    <div className="space-y-5">
      {GROUPS.map(([g, label]) => {
        const items = cases.filter((c) => c.group === g);
        if (!items.length) return null;
        return (
          <div key={g}>
            <div className="mb-2 flex items-center justify-between px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              {label}<span className="rounded-full bg-white/5 px-2 py-0.5 text-slate-400">{items.length}</span>
            </div>
            <div className="space-y-1.5">
              {items.map((c) => (
                <button key={c.txn_id} onClick={() => onSelect(c.txn_id)}
                  className={`w-full rounded-xl border px-3 py-2.5 text-left transition ${sel === c.txn_id ? "border-emerald-500/40 bg-emerald-500/[0.07]" : "border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.05]"}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium text-slate-100">{c.customer}</span>
                    <span className="font-mono text-sm text-slate-200">{inr(c.amount)}</span>
                  </div>
                  <div className="mt-0.5 flex items-center gap-1.5 text-xs text-slate-500">
                    {g !== "closed" && g !== "blocked" && <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${riskTone(c.risk)}`} title={`Breach risk ${c.risk}/100`} />}
                    <span className="truncate">{statusLabel(c)}</span>
                  </div>
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
