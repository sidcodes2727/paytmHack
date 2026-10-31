import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, FlaskConical, Sparkles } from "lucide-react";
import { api, Json, when } from "./api";
import CaseView from "./CaseView";
import Copilot, { CopilotMsg } from "./Copilot";
import Inbox from "./Inbox";
import Phone from "./Phone";

const WELCOME: CopilotMsg = {
  id: 0, from: "bot",
  text: "Hi, I'm your ops copilot. Ask why a case was routed the way it was, how a claim was verified, how the model performs, or drive the sandbox (\"fast-forward 18h\", \"bank settles this refund\").",
  chips: ["How accurate is Nirnay?", "Show evidence", "Queue status", "Show cost curve"],
};

function Sandbox({ status, sel, run }: { status: Json; sel: string | null; run: (q: string) => void }) {
  const [open, setOpen] = useState(false);
  const off = status?.offset_hours ?? 0;
  const item = (label: string, q: string, disabled = false) => (
    <button disabled={disabled} onClick={() => { run(q); setOpen(false); }} className="w-full rounded-lg px-3 py-2 text-left text-sm text-slate-200 hover:bg-white/5 disabled:opacity-40">{label}</button>
  );
  return (
    <div className="relative">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-2 rounded-xl border border-white/10 px-3 py-1.5 text-sm text-slate-300 hover:bg-white/5">
        <FlaskConical size={14} className="text-amber-300" /> Sandbox {off > 0 && <span className="font-mono text-amber-300">+{off}h</span>} <ChevronDown size={14} />
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-72 rounded-2xl border border-white/10 bg-slate-900 p-2 shadow-2xl">
          <div className="px-3 pb-2 pt-1 text-[11px] leading-relaxed text-slate-500">Simulates the outside world: the clock, the bank and the gateway. Sandbox time: <span className="text-slate-300">{when(status?.virtual_now)}</span></div>
          <div className="grid grid-cols-4 gap-1 px-2 pb-2">
            {[1, 6, 18, 24].map((h) => <button key={h} onClick={() => { run(`fast-forward ${h}h`); setOpen(false); }} className="rounded-lg bg-white/5 py-1.5 text-xs text-slate-200 hover:bg-white/10">+{h}h</button>)}
          </div>
          {item("Bank settles the selected refund", "Bank settles this refund", !sel)}
          {item("Gateway retries the selected refund", "Simulate duplicate retry", !sel)}
          <div className="my-1 border-t border-white/5" />
          {item("Reset sandbox", "reset")}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [customers, setCustomers] = useState<Json[]>([]);
  const [cid, setCid] = useState("CUST-2002");
  const [conv, setConv] = useState<Json | null>(null);
  const [cases, setCases] = useState<Json[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [detail, setDetail] = useState<Json | null>(null);
  const [status, setStatus] = useState<Json | null>(null);
  const [bench, setBench] = useState<Json | null>(null);
  const [role, setRole] = useState("FINANCE_PRIMARY");
  const [msgs, setMsgs] = useState<CopilotMsg[]>([WELCOME]);
  const [busy, setBusy] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [toast, setToast] = useState("");
  const [offline, setOffline] = useState(false);
  const nextId = useRef(1);

  const refresh = useCallback(async () => {
    try {
      const [st, cs, cv] = await Promise.all([api.status(), api.cases(), api.chat(cid)]);
      setStatus(st); setCases(cs); setConv(cv); setOffline(false);
      const pick = sel && cs.some((c: Json) => c.txn_id === sel) ? sel : cs[0]?.txn_id ?? null;
      if (pick !== sel) setSel(pick);
      setDetail(pick ? await api.getCase(pick) : null);
    } catch { setOffline(true); }
  }, [cid, sel]);

  useEffect(() => { api.customers().then(setCustomers).catch(() => undefined); api.benchmark().then(setBench).catch(() => undefined); }, []);
  useEffect(() => { refresh(); const t = setInterval(refresh, 3000); return () => clearInterval(t); }, [refresh]);
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(""), 4000); return () => clearTimeout(t); }, [toast]);

  const ask = async (q: string) => {
    setDrawer(true);
    setMsgs((m) => [...m, { id: nextId.current++, from: "user", text: q }]);
    setBusy(true);
    try {
      const r = await api.copilot(q, sel, role);
      setMsgs((m) => [...m, { id: nextId.current++, from: "bot", text: r.text, cards: r.cards, chips: r.chips }]);
      if (r.changed) { if (/reset/i.test(q)) { setSel(null); setMsgs([WELCOME]); } refresh(); }
    } catch (e) { setMsgs((m) => [...m, { id: nextId.current++, from: "bot", text: `⚠️ ${(e as Error).message}` }]); }
    setBusy(false);
  };

  // Deep link: /?ask=Show%20cost%20curve opens the copilot with that question (handy for demos).
  const asked = useRef(false);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("ask");
    if (q && !asked.current && bench) { asked.current = true; ask(q); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bench]);

  const toggleAutopilot = async () => { await api.kill(!status?.kill_switch); refresh(); };
  const autopilot = !status?.kill_switch;

  return (
    <div className="min-h-screen bg-[#070b10] text-slate-200">
      <header className="sticky top-0 z-30 border-b border-white/[0.06] bg-[#070b10]/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1920px] items-center gap-4 px-6 py-3">
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-bold tracking-tight text-white">VAADA</span>
            <span className="hidden text-xs text-slate-500 sm:inline">No proof, no close.</span>
          </div>
          {offline && <span className="rounded-full bg-rose-500/15 px-2.5 py-0.5 text-xs text-rose-300">Backend unreachable</span>}
          <div className="ml-auto flex items-center gap-3">
            <Sandbox status={status} sel={sel} run={ask} />
            <button onClick={toggleAutopilot} className="flex items-center gap-2 rounded-xl border border-white/10 px-3 py-1.5 text-sm hover:bg-white/5" title="Global kill switch for autonomous refunds">
              <span className={`relative h-4 w-7 rounded-full transition ${autopilot ? "bg-emerald-500" : "bg-rose-500"}`}>
                <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition-all ${autopilot ? "left-3.5" : "left-0.5"}`} />
              </span>
              <span className={autopilot ? "text-slate-300" : "text-rose-300"}>Autopilot {autopilot ? "on" : "paused"}</span>
            </button>
            <label className="flex items-center gap-2 rounded-xl border border-white/10 px-3 py-1.5 text-sm text-slate-400">
              Signed in as
              <select value={role} onChange={(e) => setRole(e.target.value)} className="bg-transparent text-slate-100 outline-none">
                <option className="bg-slate-900">FINANCE_PRIMARY</option><option className="bg-slate-900">FINANCE_BACKUP_LEAD</option>
              </select>
            </label>
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-[1920px] gap-6 px-6 py-6 lg:grid-cols-[380px_minmax(0,1fr)] 2xl:grid-cols-[380px_minmax(0,1fr)_400px]">
        <div className="lg:sticky lg:top-[84px] lg:self-start">
          <Phone customers={customers} cid={cid} setCid={setCid} conv={conv} sarvamLive={!!status?.sarvam_live}
            onSent={(id) => { if (id) setSel(id); refresh(); }} toast={setToast} />
        </div>

        <div className="grid min-w-0 gap-6 md:grid-cols-[260px_minmax(0,1fr)]">
          <aside className="md:sticky md:top-[84px] md:max-h-[calc(100vh-110px)] md:self-start md:overflow-y-auto">
            <div className="mb-3 px-1 text-sm font-semibold text-slate-200">Case inbox</div>
            <Inbox cases={cases} sel={sel} onSelect={setSel} />
          </aside>
          <CaseView c={detail} role={role} refresh={refresh} toast={setToast} ask={ask} />
        </div>

        <div className="hidden 2xl:block">
          <div className="sticky top-[84px] h-[calc(100vh-110px)]">
            <Copilot msgs={msgs} onAsk={ask} busy={busy} bench={bench} caseId={sel} />
          </div>
        </div>
      </main>

      <div className="2xl:hidden">
        {drawer ? (
          <div className="fixed bottom-4 right-4 z-40 h-[min(640px,calc(100vh-100px))] w-[min(420px,calc(100vw-32px))] shadow-2xl shadow-black/60">
            <div className="h-full rounded-2xl bg-[#0b1118]"><Copilot msgs={msgs} onAsk={ask} busy={busy} bench={bench} caseId={sel} onClose={() => setDrawer(false)} /></div>
          </div>
        ) : (
          <button onClick={() => setDrawer(true)} className="fixed bottom-5 right-5 z-40 flex items-center gap-2 rounded-full bg-emerald-600 px-4 py-3 text-sm font-semibold text-white shadow-xl hover:bg-emerald-500">
            <Sparkles size={16} /> Ops Copilot
          </button>
        )}
      </div>

      {toast && <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-xl border border-white/10 bg-slate-900 px-4 py-2.5 text-sm text-slate-200 shadow-2xl">{toast}</div>}
    </div>
  );
}
