import { useEffect, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { ArrowUp, X } from "lucide-react";
import { Json } from "./api";
import { Markdown } from "./md";

export type CopilotMsg = { id: number; from: "user" | "bot"; text: string; cards?: Json[]; chips?: string[] };

// Chart palette matches the paper/ink theme: ink for the primary series, sindoor for the decision point.
const C = { a: "#16140f", b: "#b5b0a4", accent: "#d2471f", grid: "#ece9e2", axis: "#8a8578" };
const axis = { stroke: C.axis, fontSize: 10, tickLine: false, axisLine: { stroke: C.grid }, fontFamily: "Geist Mono Variable, monospace" };
const tip = { contentStyle: { background: "#fff", border: "1px solid #e5e2da", borderRadius: 6, fontSize: 12, boxShadow: "0 8px 24px -12px rgba(22,20,15,.2)" } };
const legend = { wrapperStyle: { fontSize: 11, color: "#4f4b42" }, iconType: "plainline" as const };

function Chart({ kind, bench }: { kind: string; bench: Json }) {
  if (!bench) return null;
  const th = bench.thresholds;
  if (kind === "cost_curve") {
    const cc = th.cost_curve;
    const data = cc.taus.map((t: number, i: number) => ({ tau: +t.toFixed(2), Calibration: cc.issue_calibration_risk[i], OOD: cc.issue_ood_risk[i] }));
    return (
      <ResponsiveContainer width="100%" height={190}>
        <LineChart data={data} margin={{ left: -20, right: 8, top: 14 }}>
          <CartesianGrid stroke={C.grid} vertical={false} /><XAxis dataKey="tau" {...axis} interval="preserveStartEnd" minTickGap={28} /><YAxis {...axis} />
          <Tooltip {...tip} /><Legend {...legend} />
          <Line dataKey="Calibration" stroke={C.a} dot={false} strokeWidth={1.75} /><Line dataKey="OOD" stroke={C.b} dot={false} strokeWidth={1.75} strokeDasharray="5 3" />
          <ReferenceLine x={+th.tau_star_issue.toFixed(2)} stroke={C.accent} label={{ value: `τ* ${th.tau_star_issue.toFixed(2)}`, fill: C.accent, fontSize: 11, position: "top" }} />
        </LineChart>
      </ResponsiveContainer>
    );
  }
  if (kind === "reliability") {
    const cal = bench.results.ood_test.nirnay.calibration;
    const m = (bins: Json[]) => bins.filter((b) => b.count).map((b) => ({ conf: b.mean_confidence, acc: b.accuracy, n: b.count }));
    return (
      <ResponsiveContainer width="100%" height={200}>
        <ScatterChart margin={{ left: -20, right: 8, top: 8 }}>
          <CartesianGrid stroke={C.grid} /><XAxis type="number" dataKey="conf" domain={[0, 1]} {...axis} name="confidence" tickCount={6} />
          <YAxis type="number" dataKey="acc" domain={[0, 1]} {...axis} name="accuracy" /><ZAxis dataKey="n" range={[24, 160]} />
          <Tooltip {...tip} /><Legend {...legend} />
          <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke={C.b} strokeDasharray="3 3" />
          <Scatter name="Calibrated" data={m(cal.issue_reliability_bins)} fill={C.a} line={{ stroke: C.a, strokeWidth: 1.5 }} />
          <Scatter name="Uncalibrated" data={m(cal.issue_reliability_bins_uncalibrated)} fill={C.accent} line={{ stroke: C.accent, strokeWidth: 1.5 }} />
        </ScatterChart>
      </ResponsiveContainer>
    );
  }
  const g = bench.generalisation_gap;
  const data = [
    { name: "Keyword baseline", ID: g.baseline_issue_macro_f1.id, OOD: g.baseline_issue_macro_f1.ood },
    { name: "Nirnay", ID: g.nirnay_issue_macro_f1.id, OOD: g.nirnay_issue_macro_f1.ood },
  ];
  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={data} margin={{ left: -20, right: 8, top: 8 }} barGap={3}>
        <CartesianGrid stroke={C.grid} vertical={false} /><XAxis dataKey="name" {...axis} /><YAxis domain={[0, 1]} {...axis} />
        <Tooltip {...tip} cursor={{ fill: "#f6f5f1" }} /><Legend {...legend} iconType="square" />
        <Bar dataKey="ID" name="In-distribution" fill={C.b} radius={[2, 2, 0, 0]} /><Bar dataKey="OOD" name="Unseen (OOD)" fill={C.a} radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function Card({ card, bench }: { card: Json; bench: Json }) {
  if (card.type === "chart") return <div className="mt-3 rounded-md border border-line bg-card px-1 pb-1 pt-2"><Chart kind={card.chart} bench={bench} /></div>;
  if (card.type === "table")
    return (
      <div className="mt-3 overflow-hidden rounded-md border border-line bg-card">
        <table className="w-full text-[12px]">
          {card.columns.some((c: string) => c) && (
            <thead className="border-b border-line bg-[#faf9f6] text-left"><tr>{card.columns.map((c: string, i: number) => <th key={i} className="eyebrow px-2.5 py-2 font-normal">{c}</th>)}</tr></thead>
          )}
          <tbody>
            {card.rows.map((r: string[], i: number) => (
              <tr key={i} className="border-t border-line first:border-t-0 align-top">
                {r.map((v, j) => <td key={j} className={`px-2.5 py-1.5 leading-relaxed ${j === 0 ? "text-ink-2" : j === r.length - 1 ? "num text-ink" : "text-ink-2"}`}>{v}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  return null;
}

export default function Copilot({ msgs, onAsk, busy, bench, caseId, onClose }: {
  msgs: CopilotMsg[]; onAsk: (q: string) => void; busy: boolean; bench: Json; caseId: string | null; onClose?: () => void;
}) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.scrollTo({ top: 1e6, behavior: "smooth" }); }, [msgs.length, busy]);
  const last = msgs[msgs.length - 1];

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg border border-line bg-card">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <div className="flex items-baseline gap-2">
          <span className="font-serif text-[20px] leading-none text-ink">Copilot</span>
          <span className="text-[12px] text-ink-3">for the ops team</span>
        </div>
        {onClose && <button onClick={onClose} className="grid h-7 w-7 place-items-center rounded-md text-ink-3 hover:bg-sunk hover:text-ink"><X size={15} /></button>}
      </div>

      <div ref={ref} className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
        {msgs.map((m) => m.from === "user" ? (
          <div key={m.id} className="rise flex justify-end">
            <div className="max-w-[85%] rounded-[10px] rounded-br-[3px] bg-sunk px-3 py-2 text-[13px] text-ink">{m.text}</div>
          </div>
        ) : (
          <div key={m.id} className="rise text-[13px] leading-relaxed text-ink-2">
            <Markdown text={m.text} />
            {m.cards?.map((c, i) => <Card key={i} card={c} bench={bench} />)}
            {m.chips && m === last && m.chips.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {m.chips.map((c) => (
                  <button key={c} onClick={() => onAsk(c)} className="rounded-md border border-line px-2 py-1 text-[12px] text-ink-2 hover:border-ink-3 hover:text-ink">{c}</button>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="flex gap-1 py-1">{[0, 1, 2].map((i) => <span key={i} className="breathe h-1.5 w-1.5 rounded-full bg-ink-3" style={{ animationDelay: `${i * 0.18}s` }} />)}</div>}
      </div>

      <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) { onAsk(text); setText(""); } }} className="border-t border-line p-3">
        <div className="flex items-end gap-2 rounded-lg border border-line-strong bg-card p-1.5 pl-3 focus-within:border-ink-3">
          <div className="min-w-0 flex-1">
            {caseId && <div className="eyebrow pt-1">Context · <span className="text-ink-2">{caseId}</span></div>}
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask anything, or “fast-forward 18h”"
              className="w-full bg-transparent py-1 text-[13px] text-ink outline-none placeholder:text-ink-4" />
          </div>
          <button disabled={busy || !text.trim()} className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-ink text-paper hover:bg-[#2b2820] disabled:bg-line-strong"><ArrowUp size={15} /></button>
        </div>
      </form>
    </div>
  );
}
