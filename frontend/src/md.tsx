import { Fragment, ReactNode } from "react";

/** Tiny markdown subset for chat: **bold**, `code`, "- " bullets (nested with 2 spaces), line breaks. */
function inline(s: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*.+?\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  const clean = (t: string) => t.replace(/\\\*/g, "*");
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(clean(s.slice(last, m.index)));
    const t = m[0];
    if (t.startsWith("**")) out.push(<strong key={m.index} className="font-semibold text-ink">{clean(t.slice(2, -2))}</strong>);
    else out.push(<code key={m.index} className="rounded-[4px] bg-sunk px-1 py-px font-mono text-[0.86em] text-ink">{t.slice(1, -1)}</code>);
    last = m.index + t.length;
  }
  if (last < s.length) out.push(clean(s.slice(last)));
  return out;
}

export function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <div className="space-y-1.5">
      {lines.map((l, i) => {
        const t = l.trimStart();
        if (t.startsWith("- ")) {
          const nested = l.length - t.length >= 2;
          return (
            <div key={i} className={`flex gap-2 ${nested ? "pl-4 text-[0.95em] text-ink-3" : ""}`}>
              <span className={`mt-[8px] h-px w-2 shrink-0 ${nested ? "bg-ink-4" : "bg-ink-3"}`} /><span>{inline(t.slice(2))}</span>
            </div>
          );
        }
        return l.trim() ? <p key={i}>{inline(l)}</p> : <Fragment key={i} />;
      })}
    </div>
  );
}
