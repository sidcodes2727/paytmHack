import { useEffect, useRef, useState } from "react";
import { ArrowUp, Check, ChevronDown, ImagePlus, Mic, Play, Square, X } from "lucide-react";
import { api, clockTime, Json, speak } from "./api";
import { startWavRecording } from "./wavRecorder";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const SR: any = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

const initials = (n: string) => n.split(" ").map((w) => w[0]).join("");
const risky = (c: Json) => /attack|abuse/i.test(c?.persona ?? "");

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

  useEffect(() => { scroller.current?.scrollTo({ top: 1e6, behavior: "smooth" }); }, [msgs.length, busy]);
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
    <div className="mx-auto flex w-full max-w-[350px] flex-col">
      {/* customer switcher (sandbox persona) */}
      <div className="relative mb-4">
        <div className="eyebrow mb-1.5 px-1">Customer view</div>
        <button onClick={() => setMenu(!menu)} className="flex w-full items-center gap-3 rounded-lg border border-line bg-card px-3 py-2.5 text-left hover:border-line-strong">
          <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full text-[11.5px] font-semibold ${risky(me) ? "bg-bad-soft text-bad" : "bg-sunk text-ink-2"}`}>{me ? initials(me.name) : "·"}</span>
          <span className="min-w-0 flex-1">
            <span className="block text-[13.5px] font-medium text-ink">{me?.name ?? "…"}</span>
            <span className="block truncate text-[12px] text-ink-3">{me?.persona}</span>
          </span>
          <ChevronDown size={15} className="shrink-0 text-ink-3" />
        </button>
        {menu && (
          <>
            <div className="fixed inset-0 z-30" onClick={() => setMenu(false)} />
            <div className="rise absolute z-40 mt-1.5 w-full overflow-hidden rounded-lg border border-line bg-card p-1 shadow-[0_12px_40px_-12px_rgba(22,20,15,0.25)]">
              {customers.map((c) => (
                <button key={c.customer_id} onClick={() => { setCid(c.customer_id); setMenu(false); }}
                  className="flex w-full items-center gap-3 rounded-md px-2.5 py-2 text-left hover:bg-sunk">
                  <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-[10.5px] font-semibold ${risky(c) ? "bg-bad-soft text-bad" : "bg-sunk text-ink-2"}`}>{initials(c.name)}</span>
                  <span className="min-w-0 flex-1"><span className="block text-[13px] text-ink">{c.name}</span><span className="block truncate text-[11.5px] text-ink-3">{c.persona}</span></span>
                  {c.customer_id === cid && <Check size={14} className="text-ink" />}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {/* device */}
      <div className="rounded-[46px] bg-[#1a1916] p-[9px] shadow-[0_40px_80px_-30px_rgba(22,20,15,0.45),inset_0_0_0_1px_rgba(255,255,255,0.06)]">
        <div className="relative flex h-[700px] flex-col overflow-hidden rounded-[38px] bg-[#f1ede4]">
          <div className="flex items-center justify-between px-7 pb-1 pt-3 text-[12px] font-semibold text-ink">
            <span className="num">9:41</span>
            <span className="absolute left-1/2 top-2.5 h-[22px] w-[88px] -translate-x-1/2 rounded-full bg-[#1a1916]" />
            <span className="flex items-center gap-1"><span className="h-2 w-3.5 rounded-[2px] border border-ink/70" /></span>
          </div>

          <div className="flex items-center gap-3 border-b border-black/[0.06] bg-[#f8f6f1] px-4 pb-3 pt-2">
            <div className="grid h-9 w-9 place-items-center rounded-full bg-ink font-serif text-[18px] text-paper">V</div>
            <div className="flex-1">
              <div className="text-[14px] font-semibold leading-tight text-ink">Vaada Support</div>
              <div className="text-[11.5px] text-ink-3">{busy ? "typing…" : "Verified business · replies instantly"}</div>
            </div>
          </div>

          <div ref={scroller} className="flex-1 space-y-2 overflow-y-auto px-3 py-4">
            {msgs.map((m) => {
              const mine = m.from === "customer";
              return (
                <div key={m.id} className={`rise flex ${mine ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[84%] px-3 py-2 text-[13.5px] leading-[1.45] ${mine ? "rounded-[16px] rounded-br-[5px] bg-ink text-[#f6f5f1]" : "rounded-[16px] rounded-bl-[5px] bg-white text-ink shadow-[0_1px_0_rgba(22,20,15,0.06)]"}`}>
                    {m.kind === "proactive_update" && <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-wider text-sindoor">Proactive update</div>}
                    {m.attachment?.url && (
                      <div className="mb-1.5">
                        <img src={m.attachment.url} alt="screenshot" className="max-h-44 rounded-[10px]" />
                        {m.attachment.note && <div className="mt-1 text-[10.5px] opacity-60">Saved as evidence (not auto-read)</div>}
                      </div>
                    )}
                    {m.voice && mine && <div className="mb-1 flex items-center gap-1.5 text-[11px] opacity-60"><Mic size={12} /> Voice message</div>}
                    {m.text && <div className="whitespace-pre-wrap">{m.text}</div>}
                    {m.voice && !mine && (
                      <button onClick={() => speak(m.text, m.audio_b64)} className="mt-2 flex items-center gap-2 rounded-full bg-ink px-3 py-1.5 text-[12px] text-paper hover:bg-[#2b2820]">
                        <Play size={11} fill="currentColor" /> Play voice receipt <span className="opacity-50">· हिंदी</span>
                      </button>
                    )}
                    {m.chips && m === lastBot && (
                      <div className="mt-2 flex flex-col gap-1">
                        {m.chips.map((c: Json) => (
                          <button key={c.label} disabled={busy} onClick={() => send({ text: c.label, choice: c.value })}
                            className="num rounded-[10px] border border-line px-2.5 py-1.5 text-left text-[12px] text-ink hover:border-ink">{c.label}</button>
                        ))}
                      </div>
                    )}
                    <div className={`num mt-0.5 text-right text-[10px] ${mine ? "text-white/45" : "text-ink-4"}`}>{clockTime(m.at)}</div>
                  </div>
                </div>
              );
            })}
            {busy && (
              <div className="flex justify-start"><div className="flex gap-1 rounded-[16px] rounded-bl-[5px] bg-white px-3.5 py-3">
                {[0, 1, 2].map((i) => <span key={i} className="breathe h-1.5 w-1.5 rounded-full bg-ink-3" style={{ animationDelay: `${i * 0.18}s` }} />)}
              </div></div>
            )}
          </div>

          {customerTurns === 0 && me?.suggestions?.length > 0 && (
            <div className="px-3 pb-2">
              <div className="mb-1.5 px-1 text-[10.5px] font-medium uppercase tracking-wider text-ink-3">Try saying</div>
              <div className="flex flex-col gap-1">
                {me.suggestions.map((s: string) => (
                  <button key={s} onClick={() => setText(s)} className="rounded-[12px] border border-black/[0.08] bg-white/70 px-3 py-1.5 text-left text-[12px] leading-snug text-ink-2 hover:border-ink/30 hover:text-ink">{s}</button>
                ))}
              </div>
            </div>
          )}

          {image && (
            <div className="mx-3 mb-2 flex items-center gap-2 rounded-[10px] bg-white px-2.5 py-1.5 text-[12px] text-ink-2">
              <ImagePlus size={14} /> <span className="flex-1 truncate">{image.name}</span>
              <button onClick={() => setImage(null)} className="text-ink-3 hover:text-ink"><X size={14} /></button>
            </div>
          )}

          <form onSubmit={(e) => { e.preventDefault(); if ((text.trim() || image) && !busy) send({ text }); }}
            className="flex items-center gap-2 border-t border-black/[0.06] bg-[#f8f6f1] px-3 pb-5 pt-2.5">
            <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => setImage(e.target.files?.[0] ?? null)} />
            <button type="button" onClick={() => fileRef.current?.click()} className="grid h-9 w-9 place-items-center rounded-full text-ink-3 hover:bg-black/5 hover:text-ink" title="Attach screenshot"><ImagePlus size={19} /></button>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder={recording ? "Listening…" : "Message"} disabled={recording}
              className="min-w-0 flex-1 rounded-full border border-black/[0.08] bg-white px-4 py-2 text-[13.5px] text-ink outline-none placeholder:text-ink-4 focus:border-ink/30" />
            {text.trim() || image ? (
              <button disabled={busy} className="grid h-9 w-9 place-items-center rounded-full bg-ink text-paper hover:bg-[#2b2820] disabled:opacity-40"><ArrowUp size={17} /></button>
            ) : (
              <button type="button" onClick={recording ? stopVoice : startVoice} title="Voice message"
                className={`grid h-9 w-9 place-items-center rounded-full ${recording ? "bg-sindoor text-white" : "bg-ink text-paper hover:bg-[#2b2820]"}`}>
                {recording ? <Square size={12} fill="currentColor" className="breathe" /> : <Mic size={16} />}
              </button>
            )}
          </form>
        </div>
      </div>
      <p className="mt-3 px-2 text-center text-[11.5px] leading-relaxed text-ink-3">Type, speak in Hinglish, or attach a screenshot. Nothing you say is trusted on its own.</p>
    </div>
  );
}
