// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

async function req(path: string, method = "GET", body?: unknown): Promise<Json> {
  const r = await fetch(path, {
    method,
    headers: body !== undefined ? { "content-type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ?? `HTTP ${r.status}`);
  return data;
}

async function upload(path: string, file: Blob, name: string): Promise<Json> {
  const fd = new FormData();
  fd.append("file", file, name);
  const r = await fetch(path, { method: "POST", body: fd });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ?? `HTTP ${r.status}`);
  return data;
}

export const api = {
  status: () => req("/api/status"),
  benchmark: () => req("/api/benchmark"),
  customers: () => req("/api/customers"),
  chat: (cid: string) => req(`/api/chat/${cid}`),
  send: (cid: string, body: { text: string; choice?: Json; voice_mode?: string; attachment?: Json }) => req(`/api/chat/${cid}`, "POST", body),
  uploadImage: (cid: string, f: File) => upload(`/api/chat/${cid}/upload`, f, f.name),
  uploadVoice: (cid: string, b: Blob) => upload(`/api/chat/${cid}/voice`, b, "voice.wav"),
  cases: () => req("/api/cases"),
  getCase: (id: string) => req(`/api/cases/${id}`),
  approve: (id: string, role: string) => req(`/api/cases/${id}/approve`, "POST", { role }),
  reject: (id: string, role: string) => req(`/api/cases/${id}/reject`, "POST", { role }),
  copilot: (text: string, case_id: string | null, role: string) => req("/api/copilot", "POST", { text, case_id, role }),
  kill: (active: boolean) => req("/api/kill-switch", "POST", { active }),
  advance: (hours: number) => req("/api/sandbox/advance-time", "POST", { hours }),
  settle: (id: string) => req(`/api/sandbox/${id}/bank-settles`, "POST", {}),
  duplicate: (id: string) => req(`/api/sandbox/${id}/duplicate-retry`, "POST", {}),
  reset: () => req("/api/sandbox/reset", "POST", {}),
};

export const inr = (x: number | null | undefined) => (x == null ? "—" : `₹${Math.round(x).toLocaleString("en-IN")}`);
export const when = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "—";
export const clockTime = (iso?: string) => (iso ? new Date(iso).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" }) : "");

export function speak(text: string, audioB64?: string | null) {
  if (audioB64) {
    new Audio(`data:audio/wav;base64,${audioB64}`).play().catch(() => undefined);
    return;
  }
  if ("speechSynthesis" in window) {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "hi-IN";
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
  }
}

/** Plain-language labels for policy reason codes (ops-facing). */
export const REASON: Record<string, string> = {
  "POLICY_HOLD: EXCEEDS_AGENT_WALLET_CAP": "Above the ₹500 autonomous refund limit",
  HOURLY_AGENT_BUDGET_EXHAUSTED: "Hourly autonomous budget used up",
  KILL_SWITCH_ACTIVE: "Autopilot is paused",
  NIRNAY_ROUTED_HUMAN_APPROVAL: "Model recommends human approval",
  NIRNAY_ROUTED_HUMAN_INVESTIGATION: "Model recommends investigation",
  NIRNAY_ROUTED_BLOCKED: "Model recommends blocking",
  SLOW_PATH_REQUIRES_HUMAN_SIGNOFF: "Specialist-reviewed cases need sign-off",
  DUPLICATE_ACTION_BLOCKED: "Duplicate refund blocked by idempotency lock",
  ADVERSARIAL_INPUT_CONTAINED: "Prompt-injection attempt",
  SCREENSHOT_LEDGER_MISMATCH: "Customer claim contradicts bank records",
  LEDGER_SHOWS_SUCCESS_NO_REFUND_DUE: "Ledger shows payment succeeded",
  WITHIN_AGENT_WALLET: "Within autonomous limit",
  HUMAN_APPROVED: "Approved by finance",
  ROLE_NOT_AUTHORIZED: "Caller role not authorised",
  INVALID_OR_EXPIRED_APPROVAL_TOKEN: "Approval token invalid or expired",
  CLAIM_HISTORY_REVIEW: "Customer's claim history needs a human",
  MERCHANT_RECORD_CONFIRMS_REFUND: "Merchant record confirms the refund",
  FOREIGN_ORDER_CLAIMED: "Quoted another account's order",
  FOREIGN_PAYMENT_CLAIMED: "Quoted another account's payment",
  ORDER_NOT_OWNED_BY_CUSTOMER: "Order belongs to someone else",
  CLAIM_CONTRADICTS_MERCHANT_RECORD: "Claim contradicts the merchant record",
  NO_REFUND_DUE_UNDER_POLICY: "Nothing owed under merchant policy",
  ALREADY_REFUNDED: "Already refunded",
  CHARGEBACK_OPEN: "Chargeback already open (would pay twice)",
  NOTHING_LEFT_TO_REFUND: "Nothing left to refund",
};
export const reason = (c: string) => REASON[c] ?? c.replace(/_/g, " ").toLowerCase();
