import { useEffect, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { SendHorizontal, Sparkles, X } from "lucide-react";
import { Json } from "./api";
import { Markdown } from "./md";

export type CopilotMsg = { id: number; from: "user" | "bot"; text: string; cards?: Json[]; chips?: string[] };

const C = { a: "#34d399", b: "#fbbf24", c: "#fb7185", d: "#38bdf8", grid: "#1e293b", axis: "#64748b" };
const tip = { contentStyle: { background: "#0f172a", border: "1px solid #334155", borderRadius: 8, fontSize: 12 } };

function Chart({ kind, bench }: { kind: string; bench: Json }) {
  if (!bench) return null;
  const th = bench.thresholds;
  if (kind === "cost_curve") {
    const cc = th.cost_curve;
    const data = cc.taus.map((t: number, i: number) => ({ tau: +t.toFixed(2), Calibration: cc.issue_calibration_risk[i], OOD: cc.issue_ood_risk[i] }));
    return (
      <ResponsiveContainer width="100%" height={190}>
        <LineChart data={data} margin={{ left: -18, right: 8, top: 8 }}>
          <CartesianGrid stroke={C.grid} vertical={false} /><XAxis dataKey="tau" stroke={C.axis} fontSize={10} /><YAxis stroke={C.axis} fontSize={10} />
          <Tooltip {...tip} /><Legend wrapperStyle={{ fontSize: 11 }} />
          <Line dataKey="Calibration" stroke={C.a} dot={false} strokeWidth={2} /><Line dataKey="OOD" stroke={C.b} dot={false} strokeWidth={2} />
          <ReferenceLine x={+th.tau_star_issue.toFixed(2)} stroke={C.c} strokeDasharray="4 3" label={{ value: "τ*", fill: C.c, fontSize: 11, position: "top" }} />
        </LineChart>
      </ResponsiveContainer>
    );
  }
  if (kind === "reliability") {
    const cal = bench.results.ood_test.nirnay.calibration;
    const m = (bins: Json[]) => bins.filter((b) => b.count).map((b) => ({ conf: b.mean_confidence, acc: b.accuracy, n: b.count }));
    return (
      <ResponsiveContainer width="100%" height={200}>
        <ScatterChart margin={{ left: -18, right: 8, top: 8 }}>
          <CartesianGrid stroke={C.grid} /><XAxis type="number" dataKey="conf" domain={[0, 1]} stroke={C.axis} fontSize={10} name="confidence" />
          <YAxis type="number" dataKey="acc" domain={[0, 1]} stroke={C.axis} fontSize={10} name="accuracy" /><ZAxis dataKey="n" range={[30, 200]} />
          <Tooltip {...tip} /><Legend wrapperStyle={{ fontSize: 11 }} />
          <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#475569" strokeDasharray="4 4" />
          <Scatter name="Calibrated" data={m(cal.issue_reliability_bins)} fill={C.a} line />
          <Scatter name="Uncalibrated" data={m(cal.issue_reliability_bins_uncalibrated)} fill={C.c} line />
        </ScatterChart>
      </ResponsiveContainer>
    );
  }
  const g = bench.generalisation_gap;
  const data = [
    { name: "Keyword", ID: g.baseline_issue_macro_f1.id, OOD: g.baseline_issue_macro_f1.ood },
    { name: "Nirnay", ID: g.nirnay_issue_macro_f1.id, OOD: g.nirnay_issue_macro_f1.ood },
  ];
  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={data} margin={{ left: -18, right: 8, top: 8 }}>
        <CartesianGrid stroke={C.grid} vertical={false} /><XAxis dataKey="name" stroke={C.axis} fontSize={10} /><YAxis domain={[0, 1]} stroke={C.axis} fontSize={10} />
        <Tooltip {...tip} /><Legend wrapperStyle={{ fontSize: 11 }} />
        <Bar dataKey="ID" name="In-distribution" fill={C.d} radius={[4, 4, 0, 0]} /><Bar dataKey="OOD" fill={C.b} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function Card({ card, bench }: { card: Json; bench: Json }) {
  if (card.type === "chart") return <div className="mt-2 rounded-xl bg-black/20 p-2"><Chart kind={card.chart} bench={bench} /></div>;
  if (card.type === "table")
    return (
      <div className="mt-2 overflow-hidden rounded-xl border border-white/[0.06]">
        <table className="w-full text-xs">
          {card.columns.some((c: string) => c) && (
            <thead className="bg-white/[0.03] text-left text-slate-500"><tr>{card.columns.map((c: string, i: number) => <th key={i} className="px-2.5 py-1.5 font-medium">{c}</th>)}</tr></thead>
          )}
          <tbody>
            {card.rows.map((r: string[], i: number) => (
              <tr key={i} className="border-t border-white/[0.04]">
                {r.map((v, j) => <td key={j} className={`px-2.5 py-1.5 ${j === 0 ? "text-slate-400" : j === r.length - 1 ? "font-medium text-slate-100" : "text-slate-300"}`}>{v}</td>)}
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
    <div className="flex h-full flex-col rounded-2xl border border-white/[0.06] bg-white/[0.02]">
      <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-100"><Sparkles size={15} className="text-emerald-400" /> Ops Copilot</div>
        {onClose && <button onClick={onClose} className="text-slate-500 hover:text-slate-200"><X size={16} /></button>}
      </div>
      <div ref={ref} className="flex-1 space-y-3 overflow-y-auto p-4">
        {msgs.map((m) => (
          <div key={m.id} className={`flex ${m.from === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[92%] rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed ${m.from === "user" ? "rounded-br-md bg-emerald-600/90 text-white" : "rounded-bl-md bg-white/[0.04] text-slate-300"}`}>
              <Markdown text={m.text} />
              {m.cards?.map((c, i) => <Card key={i} card={c} bench={bench} />)}
              {m.chips && m === last && m.chips.length > 0 && (
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  {m.chips.map((c) => <button key={c} onClick={() => onAsk(c)} className="rounded-full border border-white/10 px-2.5 py-1 text-[11px] text-slate-300 hover:bg-white/5">{c}</button>)}
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && <div className="w-12 rounded-2xl bg-white/[0.04] px-3 py-2 text-slate-500"><span className="animate-pulse">●●●</span></div>}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) { onAsk(text); setText(""); } }} className="border-t border-white/[0.06] p-3">
        {caseId && <div className="mb-2 text-[11px] text-slate-500">Context: <span className="font-mono text-slate-400">{caseId}</span></div>}
        <div className="flex gap-2">
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask about this case, metrics, or say “fast-forward 18h”"
            className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-emerald-500/50" />
          <button disabled={busy} className="grid w-10 place-items-center rounded-xl bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50"><SendHorizontal size={15} /></button>
        </div>
      </form>
    </div>
  );
}
