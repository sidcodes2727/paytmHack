import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, MessageSquareText } from "lucide-react";
import { api, Json, when } from "./api";
import CaseView from "./CaseView";
import Copilot, { CopilotMsg } from "./Copilot";
import Inbox from "./Inbox";
import Phone from "./Phone";
import { Dot } from "./ui";

const WELCOME: CopilotMsg = {
  id: 0, from: "bot",
  text: "Ask why a case was routed the way it was, how a claim was verified, or how the model performs. You can also drive the sandbox: \"fast-forward 18h\", \"bank settles this refund\".",
  chips: ["Show evidence", "How accurate is Nirnay?", "Queue status", "Show cost curve"],
};

function Sandbox({ status, sel, run }: { status: Json; sel: string | null; run: (q: string) => void }) {
  const [open, setOpen] = useState(false);
  const off = status?.offset_hours ?? 0;
  const item = (label: string, hint: string, q: string, disabled = false) => (
    <button disabled={disabled} onClick={() => { run(q); setOpen(false); }}
      className="flex w-full items-baseline justify-between gap-4 rounded-md px-2.5 py-2 text-left text-[13px] text-ink hover:bg-sunk disabled:text-ink-4 disabled:hover:bg-transparent">
      {label}<span className="text-[11.5px] text-ink-3">{hint}</span>
    </button>
  );
  return (
    <div className="relative">
      <button onClick={() => setOpen(!open)} className="flex h-8 items-center gap-2 rounded-md px-2.5 text-[13px] text-ink-2 hover:bg-sunk hover:text-ink">
        Sandbox{off > 0 && <span className="num rounded-[4px] bg-warn-soft px-1.5 py-px text-[11.5px] text-warn">+{off}h</span>}<ChevronDown size={14} className="text-ink-3" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="rise absolute right-0 z-40 mt-1.5 w-80 rounded-lg border border-line bg-card p-1.5 shadow-[0_12px_40px_-12px_rgba(22,20,15,0.25)]">
            <div className="px-2.5 pb-2 pt-1.5">
              <div className="eyebrow">Simulated world</div>
              <p className="mt-1 text-[12px] leading-relaxed text-ink-3">The clock, bank and merchants are a sandbox. Now: <span className="num text-ink-2">{when(status?.virtual_now)}</span></p>
            </div>
            <div className="grid grid-cols-4 gap-1 px-1.5 pb-1.5">
              {[1, 6, 18, 24].map((h) => (
                <button key={h} onClick={() => { run(`fast-forward ${h}h`); setOpen(false); }} className="num h-8 rounded-md border border-line text-[12px] text-ink hover:bg-sunk">+{h}h</button>
              ))}
            </div>
            <div className="my-1 h-px bg-line" />
            {item("Credit the selected refund", "bank / merchant", "Bank settles this refund", !sel)}
            {item("Replay the refund request", "duplicate retry", "Simulate duplicate retry", !sel)}
            <div className="my-1 h-px bg-line" />
            {item("Reset sandbox", "clears all cases", "reset")}
          </div>
        </>
      )}
    </div>
  );
}

export default function App() {
  const [customers, setCustomers] = useState<Json[]>([]);
  const [cid, setCid] = useState("CUST-2002");
  const [conv, setConv] = useState<Json | null>(null);
  const [cases, setCases] = useState<Json[]>([]);
  const [sel, setSel] = useState<string | null>(() => new URLSearchParams(window.location.search).get("case"));
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
    } catch (e) { setMsgs((m) => [...m, { id: nextId.current++, from: "bot", text: `Couldn't do that: ${(e as Error).message}` }]); }
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
  const open = cases.filter((c) => c.group !== "closed" && c.group !== "blocked").length;

  return (
    <div className="min-h-screen bg-paper text-ink lg:flex lg:h-screen lg:flex-col lg:overflow-hidden">
      <header className="sticky top-0 z-30 shrink-0 border-b border-line bg-paper/95 backdrop-blur-sm">
        <div className="mx-auto flex h-14 max-w-[1920px] items-center gap-5 px-6">
          <div className="flex items-baseline gap-2.5">
            <span className="font-serif text-[26px] leading-none tracking-tight text-ink">Vaada</span>
            <span className="text-[13px] text-ink-3">वादा</span>
          </div>
          <span className="hidden h-5 w-px bg-line-strong md:block" />
          <span className="hidden text-[13px] text-ink-3 md:block">Every refund is a promise. <span className="text-ink-2">No proof, no close.</span></span>
          {offline && <span className="flex items-center gap-1.5 rounded-[5px] bg-bad-soft px-2 py-1 text-[12px] text-bad"><Dot tone="red" /> Backend unreachable</span>}

          <div className="ml-auto flex items-center gap-1">
            <span className="mr-3 hidden items-center gap-1.5 text-[12.5px] text-ink-3 lg:flex">
              <span className="num text-ink">{open}</span> open · <span className="num text-ink">{cases.length - open}</span> resolved
            </span>
            <Sandbox status={status} sel={sel} run={ask} />
            <button onClick={toggleAutopilot} title="Global kill switch for autonomous refunds"
              className="flex h-8 items-center gap-2 rounded-md px-2.5 text-[13px] text-ink-2 hover:bg-sunk hover:text-ink">
              <span className={`relative h-[16px] w-[28px] rounded-full transition-colors ${autopilot ? "bg-ink" : "bg-line-strong"}`}>
                <span className={`absolute top-[2px] h-3 w-3 rounded-full bg-paper transition-all ${autopilot ? "left-[14px]" : "left-[2px]"}`} />
              </span>
              Autopilot <span className={autopilot ? "text-ink" : "font-medium text-bad"}>{autopilot ? "on" : "paused"}</span>
            </button>
            <span className="mx-2 h-5 w-px bg-line" />
            <label className="flex h-8 items-center gap-2 rounded-md border border-line bg-card pl-2.5 pr-1.5 text-[12.5px] text-ink-3">
              Signed in as
              <select value={role} onChange={(e) => setRole(e.target.value)} className="num cursor-pointer bg-transparent pr-1 text-[12px] text-ink outline-none">
                <option>FINANCE_PRIMARY</option><option>FINANCE_BACKUP_LEAD</option>
              </select>
            </label>
          </div>
        </div>
      </header>

      <main className="mx-auto grid w-full max-w-[1920px] gap-x-6 gap-y-6 px-6 py-6 lg:min-h-0 lg:flex-1 lg:grid-rows-1 lg:py-0 lg:grid-cols-[360px_minmax(0,1fr)] 2xl:grid-cols-[360px_minmax(0,1fr)_400px]">
        <div className="lg:h-full lg:min-h-0 lg:overflow-y-auto lg:py-6 lg:pr-1">
          <Phone customers={customers} cid={cid} setCid={setCid} conv={conv} sarvamLive={!!status?.sarvam_live}
            onSent={(id) => { if (id) setSel(id); refresh(); }} toast={setToast} />
        </div>

        <div className="grid min-w-0 gap-6 md:grid-cols-[264px_minmax(0,1fr)] lg:h-full lg:min-h-0 lg:grid-rows-1">
          <aside className="lg:h-full lg:min-h-0 lg:overflow-y-auto lg:py-6 lg:pr-1">
            <div className="mb-3 flex items-baseline justify-between px-1">
              <span className="font-serif text-[22px] leading-none text-ink">Inbox</span>
              <span className="eyebrow">{cases.length} case{cases.length === 1 ? "" : "s"}</span>
            </div>
            <Inbox cases={cases} sel={sel} onSelect={setSel} />
          </aside>
          <div className="min-w-0 lg:h-full lg:min-h-0 lg:overflow-y-auto lg:py-6 lg:pr-2">
            <CaseView c={detail} role={role} refresh={refresh} toast={setToast} ask={ask} />
          </div>
        </div>

        <div className="hidden 2xl:block 2xl:h-full 2xl:min-h-0 2xl:py-6">
          <div className="h-full">
            <Copilot msgs={msgs} onAsk={ask} busy={busy} bench={bench} caseId={sel} />
          </div>
        </div>
      </main>

      <div className="2xl:hidden">
        {drawer ? (
          <div className="rise fixed bottom-4 right-4 z-40 h-[min(660px,calc(100vh-96px))] w-[min(420px,calc(100vw-32px))] shadow-[0_24px_60px_-20px_rgba(22,20,15,0.35)]">
            <Copilot msgs={msgs} onAsk={ask} busy={busy} bench={bench} caseId={sel} onClose={() => setDrawer(false)} />
          </div>
        ) : (
          <button onClick={() => setDrawer(true)} className="fixed bottom-5 right-5 z-40 flex h-11 items-center gap-2 rounded-full bg-ink px-4 text-[13px] font-medium text-paper shadow-[0_12px_30px_-10px_rgba(22,20,15,0.5)] hover:bg-[#2b2820]">
            <MessageSquareText size={16} /> Ops copilot
          </button>
        )}
      </div>

      {toast && <div className="rise fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-md bg-ink px-4 py-2.5 text-[13px] text-paper shadow-xl">{toast}</div>}
    </div>
  );
}
