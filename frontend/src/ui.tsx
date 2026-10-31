import { ReactNode } from "react";

const tones: Record<string, string> = {
  green: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/25",
  amber: "bg-amber-500/10 text-amber-300 ring-amber-500/25",
  red: "bg-rose-500/10 text-rose-300 ring-rose-500/25",
  blue: "bg-sky-500/10 text-sky-300 ring-sky-500/25",
  slate: "bg-slate-500/10 text-slate-300 ring-slate-500/25",
};

export function Pill({ tone = "slate", children }: { tone?: string; children: ReactNode }) {
  return <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${tones[tone] ?? tones.slate}`}>{children}</span>;
}

export function Panel({ title, action, children, className = "" }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-2xl border border-white/[0.06] bg-white/[0.02] ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-2 px-5 pt-4">
          <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
          {action}
        </header>
      )}
      <div className="p-5 pt-3">{children}</div>
    </section>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="py-1.5">
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-0.5 text-sm text-slate-200">{children}</div>
    </div>
  );
}
