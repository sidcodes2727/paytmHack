import { ButtonHTMLAttributes, ReactNode } from "react";

const tones: Record<string, string> = {
  green: "bg-ok-soft text-ok",
  amber: "bg-warn-soft text-warn",
  red: "bg-bad-soft text-bad",
  blue: "bg-info-soft text-info",
  slate: "bg-sunk text-ink-2",
  accent: "bg-sindoor-soft text-sindoor",
};
const dots: Record<string, string> = {
  green: "bg-ok", amber: "bg-warn", red: "bg-bad", blue: "bg-info", slate: "bg-ink-3", accent: "bg-sindoor",
};

/** Status tag: small, square-ish, a dot and a word. */
export function Pill({ tone = "slate", children }: { tone?: string; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-[5px] px-1.5 py-[3px] text-[11.5px] font-medium leading-none ${tones[tone] ?? tones.slate}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${dots[tone] ?? dots.slate}`} />{children}
    </span>
  );
}

export function Dot({ tone = "slate", pulse = false }: { tone?: string; pulse?: boolean }) {
  return <span className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${dots[tone] ?? dots.slate} ${pulse ? "breathe" : ""}`} />;
}

export function Panel({ title, action, children, className = "", flush = false }: {
  title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; flush?: boolean;
}) {
  return (
    <section className={`rounded-lg border border-line bg-card ${className}`}>
      {title && (
        <header className="flex min-h-11 items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <h3 className="text-[13px] font-medium text-ink">{title}</h3>
          {action}
        </header>
      )}
      <div className={flush ? "" : "p-4"}>{children}</div>
    </section>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[92px_1fr] gap-3 py-2 first:pt-0 last:pb-0">
      <div className="eyebrow pt-[3px]">{label}</div>
      <div className="text-[13px] leading-relaxed text-ink">{children}</div>
    </div>
  );
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost"; size?: "sm" | "md" };
export function Button({ variant = "secondary", size = "md", className = "", ...p }: BtnProps) {
  const v = {
    primary: "bg-ink text-paper hover:bg-[#2b2820] disabled:bg-ink-4",
    secondary: "border border-line-strong bg-card text-ink hover:bg-sunk disabled:text-ink-4",
    ghost: "text-ink-2 hover:bg-sunk hover:text-ink",
  }[variant];
  const s = size === "sm" ? "h-7 px-2.5 text-[12px]" : "h-8 px-3 text-[13px]";
  return <button {...p} className={`inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:cursor-not-allowed ${v} ${s} ${className}`} />;
}
