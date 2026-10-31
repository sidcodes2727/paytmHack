"""Sarvam AI wrappers (STT, document OCR, chat, TTS) with a resilient offline fallback.

Every response carries `mode`: "LIVE_SARVAM" or "SIMULATED_CACHED_FALLBACK".
OCR uses Sarvam Document Intelligence (doc-ai); SARVAM_OCR_URL optionally overrides the digitise endpoint.
"""
import base64
import os
import re
import time

import httpx

import config  # noqa: F401  (loads backend/.env before env vars are read)

BASE = os.environ.get("SARVAM_BASE_URL") or "https://api.sarvam.ai"
TIMEOUT = 8.0
SIM = "SIMULATED_CACHED_FALLBACK"
LIVE = "LIVE_SARVAM"


def _key() -> str | None:
    return os.environ.get("SARVAM_API_KEY") or None


def has_key() -> bool:
    return _key() is not None


def _headers() -> dict:
    k = _key()
    return {"api-subscription-key": k, "Authorization": f"Bearer {k}"}


def speech_to_text(audio_bytes: bytes | None, cached_transcript: str, filename: str = "voice.wav") -> dict:
    if _key() and audio_bytes:
        try:
            r = httpx.post(f"{BASE}/speech-to-text", headers=_headers(), timeout=TIMEOUT,
                           files={"file": (filename, audio_bytes)},
                           data={"model": "saaras:v3", "mode": "translit", "language_code": "hi-IN"})  # Roman-script Hinglish
            r.raise_for_status()
            return {"transcript": r.json().get("transcript", ""), "mode": LIVE, "model": "saaras:v3"}
        except Exception as e:
            return {"transcript": cached_transcript, "mode": SIM, "model": "saarika", "error": str(e)[:120]}
    return {"transcript": cached_transcript, "mode": SIM, "model": "saarika"}


DOC_AI = os.environ.get("SARVAM_OCR_URL") or "https://api.sarvam.ai/doc-ai/v1/job/digitise"


def parse_receipt_text(text: str) -> dict:
    """Pull amount / UTR / status out of OCR'd UPI screenshot text. Everything returned is an untrusted *claim*."""
    amt = None
    m = re.search(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", text, re.I)
    if m:
        amt = float(m.group(1).replace(",", ""))
    else:
        nums = [float(x.replace(",", "")) for x in re.findall(r"\b\d{1,3}(?:,\d{2,3})+(?:\.\d+)?\b|\b\d+\.\d{2}\b", text)]
        amt = max(nums) if nums else None
    u = re.search(r"(?:utr|ref(?:erence)?(?:\s*(?:no|number|id))?)\D{0,12}(\d{12})\b", text, re.I) or re.search(r"\b(\d{12})\b", text)
    low = text.lower()
    status = ("FAILED" if re.search(r"fail|unsuccess|declin|reject", low) else
              "PENDING" if re.search(r"pending|processing|in progress", low) else
              "SUCCESS" if re.search(r"success|completed|paid|credited", low) else None)
    return {"amount": amt, "utr": u.group(1) if u else None, "status": status}


def _doc_ai_once(image_bytes: bytes, filename: str, h: dict) -> str:
    base = DOC_AI.rsplit("/", 1)[0]
    mime = "image/jpeg" if filename.lower().endswith((".jpg", ".jpeg")) else "image/png"
    r = httpx.post(DOC_AI, headers=h, timeout=30, files={"file": (filename, image_bytes, mime)}, data={"language": "en-IN"})
    r.raise_for_status()
    jid = r.json()["job_id"]
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        st = httpx.get(f"{base}/{jid}/status", headers=h, timeout=15).json().get("status")
        if st in ("completed", "partially_completed"):
            break
        if st in ("failed", "rejected"):
            raise RuntimeError(f"doc-ai job {st}")
        time.sleep(1.0)
    else:
        raise TimeoutError("doc-ai job did not finish in 40s")
    res = httpx.get(f"{base}/{jid}/results", headers=h, params={"format": "json"}, timeout=30)
    res.raise_for_status()
    return "\n".join(b["text"] for d in res.json().get("documents", []) for p in d.get("pages", [])
                     for b in p.get("blocks", []) if b.get("text"))


def document_ocr(image_bytes: bytes | None, cached: dict, filename: str = "screenshot.png") -> dict:
    """Sarvam Document Intelligence (doc-ai): upload -> poll -> results(json) -> parse text blocks. One retry on failure."""
    if not (_key() and image_bytes):
        return {**cached, "mode": SIM}
    # doc-ai authenticates with api-subscription-key only; an extra Bearer header is rejected with 401.
    h = {"api-subscription-key": _key()}
    err = ""
    for attempt in range(2):
        try:
            text = _doc_ai_once(image_bytes, filename, h)
            return {**parse_receipt_text(text), "embedded_text": text[:1500], "mode": LIVE}
        except Exception as e:
            err = str(e)[:120]
            time.sleep(1.0)
    return {**cached, "mode": SIM, "error": err}


def chat(messages: list[dict], model: str = "sarvam-105b", temperature: float = 0.1, max_tokens: int = 1000) -> dict:
    if _key():
        try:
            r = httpx.post(f"{BASE}/v1/chat/completions", headers=_headers(), timeout=30,
                           json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                                 "reasoning_effort": None})   # sarvam-105b thinks by default; off for fast replies
            r.raise_for_status()
            return {"content": r.json()["choices"][0]["message"]["content"], "mode": LIVE, "model": model}
        except Exception as e:
            return {"content": None, "mode": SIM, "error": str(e)[:120]}
    return {"content": None, "mode": SIM}


def text_to_speech(text: str, lang: str = "hi-IN") -> dict:
    if _key():
        try:
            r = httpx.post(f"{BASE}/text-to-speech", headers=_headers(), timeout=TIMEOUT,
                           json={"text": text, "target_language_code": lang, "model": "bulbul:v3"})
            r.raise_for_status()
            audio = r.json()["audios"][0]
            base64.b64decode(audio, validate=True)
            return {"text": text, "audio_b64": audio, "audio_mime": "audio/wav", "mode": LIVE, "model": "bulbul"}
        except Exception as e:
            return {"text": text, "audio_b64": None, "mode": SIM, "model": "bulbul", "error": str(e)[:120]}
    # Offline: no audio bytes are fabricated; the UI speaks `text` with the browser's hi-IN voice.
    return {"text": text, "audio_b64": None, "audio_mime": None, "mode": SIM, "model": "bulbul",
            "playback_hint": "browser_speech_synthesis_hi-IN"}
