"""VAADA API (thin layer over orchestrator / conversation / copilot)."""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

import clock
import config
import conversation
import copilot
import data_and_nirnay as dn
import orchestrator as orc
import policy_gate
import sarvam_adapter
import scenarios

UPLOADS = config.DATA_DIR / "uploads"
UPLOADS.mkdir(exist_ok=True)


async def radar_loop():
    while True:
        await asyncio.sleep(5)
        try:
            orc.radar_tick()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    orc.STATE["model"], orc.STATE["bench"] = dn.initialize()
    orc.reset()
    task = asyncio.create_task(radar_loop())
    yield
    task.cancel()


app = FastAPI(title="VAADA", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _err(fn, *a):
    try:
        return fn(*a)
    except orc.ActionError as e:
        raise HTTPException(409, str(e))


# ------------------------------------------------------------------ system
@app.get("/api/status")
def status():
    return {"sarvam_live": sarvam_adapter.has_key(), "kill_switch": policy_gate.KILL_SWITCH_ACTIVE,
            "offset_hours": clock.offset_hours(), "virtual_now": clock.now().isoformat(),
            "approvers": [config.PRIMARY_APPROVER, config.BACKUP_APPROVER]}


@app.get("/api/benchmark")
def benchmark():
    return orc.STATE["bench"]


# ------------------------------------------------------------------ customer side
class ChatReq(BaseModel):
    text: str = ""
    choice: Optional[dict] = None
    voice_mode: Optional[str] = None
    attachment: Optional[dict] = None


@app.get("/api/customers")
def customers():
    return scenarios.public_customers()


@app.get("/api/chat/{customer_id}")
def get_chat(customer_id: str):
    if customer_id not in scenarios.BY_ID:
        raise HTTPException(404, "unknown customer")
    return orc.load_conversation(customer_id)


@app.post("/api/chat/{customer_id}")
def post_chat(customer_id: str, req: ChatReq):
    if customer_id not in scenarios.BY_ID:
        raise HTTPException(404, "unknown customer")
    if not req.text.strip() and not req.attachment and not req.choice:
        raise HTTPException(400, "empty message")
    return conversation.post_message(customer_id, req.text, choice=req.choice, voice_mode=req.voice_mode,
                                     attachment=req.attachment)


@app.post("/api/chat/{customer_id}/upload")
async def upload(customer_id: str, file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "file too large (8 MB max)")
    ext = (file.filename or "img.png").rsplit(".", 1)[-1].lower()[:5]
    name = f"{uuid.uuid4().hex}.{ext}"
    (UPLOADS / name).write_bytes(data)
    ocr = sarvam_adapter.document_ocr(data, {}, filename=file.filename or "screenshot.png")
    parsed = ocr.get("mode") == sarvam_adapter.LIVE and ocr.get("amount") is not None
    return {"url": f"/api/uploads/{name}", "ocr": ocr if parsed else None,
            "ocr_mode": ocr.get("mode"), "note": None if parsed else "OCR offline: screenshot stored as untrusted evidence only"}


@app.get("/api/uploads/{name}")
def get_upload(name: str):
    p = (UPLOADS / name).resolve()
    if p.parent != UPLOADS.resolve() or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.post("/api/chat/{customer_id}/voice")
async def voice(customer_id: str, file: UploadFile = File(...)):
    data = await file.read()
    r = sarvam_adapter.speech_to_text(data, "", filename=file.filename or "voice.webm")
    if r["mode"] != sarvam_adapter.LIVE or not r["transcript"]:
        raise HTTPException(503, "Sarvam STT unavailable; use browser dictation")
    return r


# ------------------------------------------------------------------ ops side
class RoleReq(BaseModel):
    role: str


class CopilotReq(BaseModel):
    text: str
    case_id: Optional[str] = None
    role: str = config.PRIMARY_APPROVER


class KillReq(BaseModel):
    active: bool


class HoursReq(BaseModel):
    hours: float


@app.get("/api/cases")
def cases():
    return orc.case_summaries()


@app.get("/api/cases/{txn_id}")
def case(txn_id: str):
    c = orc.view_case(txn_id)
    if not c:
        raise HTTPException(404, "case not found")
    return c


@app.post("/api/cases/{txn_id}/approve")
def approve(txn_id: str, req: RoleReq):
    _err(orc.approve, txn_id, req.role)
    return {"ok": True}


@app.post("/api/cases/{txn_id}/reject")
def reject(txn_id: str, req: RoleReq):
    _err(orc.reject, txn_id, req.role)
    return {"ok": True}


@app.post("/api/copilot")
def copilot_chat(req: CopilotReq):
    return copilot.handle(req.text, req.case_id, req.role)


@app.post("/api/kill-switch")
def kill(req: KillReq):
    policy_gate.set_kill_switch(req.active)
    return {"kill_switch": policy_gate.KILL_SWITCH_ACTIVE}


# ------------------------------------------------------------------ sandbox (simulated external world)
@app.post("/api/sandbox/advance-time")
def advance(req: HoursReq):
    return {"offset_hours": _err(orc.advance_time, req.hours)}


@app.post("/api/sandbox/{txn_id}/bank-settles")
def settle(txn_id: str):
    return _err(orc.bank_settles, txn_id)


@app.post("/api/sandbox/{txn_id}/duplicate-retry")
def dup(txn_id: str):
    return _err(orc.duplicate_retry, txn_id)


@app.post("/api/sandbox/reset")
def reset():
    orc.reset()
    return {"ok": True}
