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
    if (t.startsWith("**")) out.push(<strong key={m.index} className="font-semibold text-white">{clean(t.slice(2, -2))}</strong>);
    else out.push(<code key={m.index} className="rounded bg-white/10 px-1 py-0.5 font-mono text-[0.85em]">{t.slice(1, -1)}</code>);
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
            <div key={i} className={`flex gap-2 ${nested ? "pl-4 text-[0.95em] text-slate-400" : ""}`}>
              <span className={`mt-[7px] h-1 w-1 shrink-0 rounded-full ${nested ? "bg-slate-600" : "bg-slate-500"}`} /><span>{inline(t.slice(2))}</span>
            </div>
          );
        }
        return l.trim() ? <p key={i}>{inline(l)}</p> : <Fragment key={i} />;
      })}
    </div>
  );
}
