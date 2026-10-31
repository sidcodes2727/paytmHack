import { useEffect, useRef, useState } from "react";
import { ChevronDown, ImagePlus, Mic, Play, SendHorizontal, ShieldCheck, Square, X } from "lucide-react";
import { api, clockTime, Json, speak } from "./api";
import { startWavRecording } from "./wavRecorder";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const SR: any = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

export default function Phone({ customers, cid, setCid, conv, sarvamLive, onSent, toast }: {
  customers: Json[]; cid: string; setCid: (c: string) => void; conv: Json | null; sarvamLive: boolean;
  onSent: (caseId: string | null) => void; toast: (s: string) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [image, setImage] = useState<File | null>(null);
  const [recording, setRecording] = useState(false);
  const [menu, setMenu] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const recRef = useRef<Json>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const me = customers.find((c) => c.customer_id === cid);
  const msgs: Json[] = conv?.messages ?? [];
  const customerTurns = msgs.filter((m) => m.from === "customer").length;

  useEffect(() => { scroller.current?.scrollTo({ top: 1e6, behavior: "smooth" }); }, [msgs.length]);
  useEffect(() => { setText(""); setImage(null); }, [cid]);

  const send = async (body: { text: string; choice?: Json; voice_mode?: string }) => {
    setBusy(true);
    try {
      let attachment;
      if (image) {
        const up = await api.uploadImage(cid, image);
        attachment = { url: up.url, ocr: up.ocr, note: up.note, name: image.name };
      }
      const r = await api.send(cid, { ...body, attachment });
      setText(""); setImage(null);
      onSent(r.case_id ?? null);
    } catch (e) { toast((e as Error).message); }
    setBusy(false);
  };

  const startVoice = async () => {
    if (sarvamLive && navigator.mediaDevices) {
      try {
        const rec = await startWavRecording();
        recRef.current = {
          stop: async () => {
            setBusy(true);
            try {
              const r = await api.uploadVoice(cid, await rec.stop());
              await send({ text: r.transcript, voice_mode: "LIVE_SARVAM" });
            } catch (e) { toast(`Voice: ${(e as Error).message}`); setBusy(false); }
          },
        };
        setRecording(true);
      } catch { toast("Microphone permission was denied."); }
      return;
    }
    if (!SR) { toast("Voice needs a Sarvam key or a browser with speech recognition (Chrome)."); return; }
    const rec = new SR();
    rec.lang = "hi-IN";
    rec.interimResults = false;
    rec.onresult = (e: Json) => {
      const t = Array.from(e.results).map((r: Json) => r[0].transcript).join(" ");
      if (t.trim()) send({ text: t, voice_mode: "BROWSER_SPEECH" });
    };
    rec.onerror = (e: Json) => toast(`Voice: ${e.error}`);
    rec.onend = () => setRecording(false);
    rec.start();
    recRef.current = rec;
    setRecording(true);
  };
  const stopVoice = () => { recRef.current?.stop(); setRecording(false); };

  const lastBot = [...msgs].reverse().find((m) => m.from === "vaada");

  return (
    <div className="flex flex-col items-center">
      <div className="relative mb-3 w-full max-w-[360px]">
        <button onClick={() => setMenu(!menu)} className="flex w-full items-center justify-between rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-left hover:bg-white/[0.06]">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-slate-500">Customer view</div>
            <div className="text-sm font-medium text-slate-100">{me?.name ?? "…"} <span className="text-slate-500">· {me?.persona}</span></div>
          </div>
          <ChevronDown size={16} className="text-slate-500" />
        </button>
        {menu && (
          <div className="absolute z-30 mt-1 w-full overflow-hidden rounded-xl border border-white/10 bg-slate-900 shadow-2xl">
            {customers.map((c) => (
              <button key={c.customer_id} onClick={() => { setCid(c.customer_id); setMenu(false); }}
                className={`flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-white/5 ${c.customer_id === cid ? "bg-white/5" : ""}`}>
                <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full text-xs font-semibold ${/attack|abuse/i.test(c.persona) ? "bg-rose-500/15 text-rose-300" : "bg-emerald-500/15 text-emerald-300"}`}>
                  {c.name.split(" ").map((w: string) => w[0]).join("")}
                </span>
                <span className="min-w-0"><span className="block text-sm text-slate-100">{c.name}</span><span className="block text-xs text-slate-500">{c.persona}</span></span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex h-[720px] w-full max-w-[360px] flex-col overflow-hidden rounded-[2.2rem] border-[6px] border-slate-800 bg-[#0b1014] shadow-2xl shadow-black/50">
        <div className="flex items-center gap-3 bg-[#111a20] px-4 pb-3 pt-4">
          <div className="grid h-9 w-9 place-items-center rounded-full bg-emerald-500/20 text-sm font-bold text-emerald-300">V</div>
          <div className="flex-1">
            <div className="flex items-center gap-1 text-sm font-semibold text-slate-100">VAADA Support <ShieldCheck size={13} className="text-emerald-400" /></div>
            <div className="text-[11px] text-slate-500">usually replies instantly</div>
          </div>
        </div>

        <div ref={scroller} className="flex-1 space-y-2 overflow-y-auto px-3 py-4">
          {msgs.map((m) => {
            const mine = m.from === "customer";
            return (
              <div key={m.id} className={`flex ${mine ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[85%] rounded-2xl px-3 py-2 text-[13.5px] leading-relaxed ${mine ? "rounded-br-md bg-emerald-700/90 text-white" : "rounded-bl-md bg-[#1c262d] text-slate-100"}`}>
                  {m.kind === "proactive_update" && <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-amber-300">Proactive update</div>}
                  {m.attachment?.url && (
                    <div className="mb-1.5">
                      <img src={m.attachment.url} alt="screenshot" className="max-h-44 rounded-lg" />
                      {m.attachment.note && <div className="mt-1 text-[10px] text-emerald-100/70">Not auto-read (OCR offline). Saved as evidence.</div>}
                    </div>
                  )}
                  {m.voice && mine && <div className="mb-1 flex items-center gap-1.5 text-[11px] text-emerald-100/80"><Mic size={12} /> Voice message</div>}
                  {m.text && <div className="whitespace-pre-wrap">{m.text}</div>}
                  {m.voice && !mine && (
                    <button onClick={() => speak(m.text, m.audio_b64)} className="mt-2 flex items-center gap-2 rounded-full bg-emerald-500/15 px-3 py-1 text-xs text-emerald-200 hover:bg-emerald-500/25">
                      <Play size={12} /> Play Hindi voice receipt
                    </button>
                  )}
                  {m.chips && m === lastBot && (
                    <div className="mt-2 flex flex-col gap-1.5">
                      {m.chips.map((c: Json) => (
                        <button key={c.label} disabled={busy} onClick={() => send({ text: c.label, choice: c.value })}
                          className="rounded-xl border border-emerald-500/30 px-3 py-1.5 text-left text-xs text-emerald-200 hover:bg-emerald-500/10">{c.label}</button>
                      ))}
                    </div>
                  )}
                  <div className={`mt-0.5 text-right text-[10px] ${mine ? "text-emerald-100/60" : "text-slate-500"}`}>{clockTime(m.at)}</div>
                </div>
              </div>
            );
          })}
          {busy && <div className="flex justify-start"><div className="rounded-2xl bg-[#1c262d] px-4 py-2.5 text-slate-400"><span className="animate-pulse">●●●</span></div></div>}
        </div>

        {customerTurns === 0 && me?.suggestions?.length > 0 && (
          <div className="px-3 pb-2">
            <div className="mb-1.5 px-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500">Try saying</div>
            <div className="flex flex-col gap-1.5">
              {me.suggestions.map((s: string) => (
                <button key={s} onClick={() => setText(s)} className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-1.5 text-left text-[11.5px] leading-snug text-slate-300 hover:bg-white/[0.08]">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {image && (
          <div className="mx-3 mb-2 flex items-center gap-2 rounded-lg bg-white/5 px-2 py-1.5 text-xs text-slate-300">
            <ImagePlus size={14} /> <span className="flex-1 truncate">{image.name}</span>
            <button onClick={() => setImage(null)}><X size={14} /></button>
          </div>
        )}

        <form onSubmit={(e) => { e.preventDefault(); if ((text.trim() || image) && !busy) send({ text }); }} className="flex items-center gap-2 bg-[#111a20] px-3 py-3">
          <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => setImage(e.target.files?.[0] ?? null)} />
          <button type="button" onClick={() => fileRef.current?.click()} className="text-slate-400 hover:text-slate-200" title="Attach screenshot"><ImagePlus size={20} /></button>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder={recording ? "Listening…" : "Message"} disabled={recording}
            className="min-w-0 flex-1 rounded-full bg-[#1c262d] px-4 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-500" />
          {text.trim() || image ? (
            <button disabled={busy} className="grid h-9 w-9 place-items-center rounded-full bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50"><SendHorizontal size={16} /></button>
          ) : (
            <button type="button" onClick={recording ? stopVoice : startVoice} title="Voice message"
              className={`grid h-9 w-9 place-items-center rounded-full text-white ${recording ? "animate-pulse bg-rose-600" : "bg-emerald-600 hover:bg-emerald-500"}`}>
              {recording ? <Square size={14} /> : <Mic size={16} />}
            </button>
          )}
        </form>
      </div>
    </div>
  );
}
