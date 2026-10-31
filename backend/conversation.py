"""Customer chat: free-form text/voice/screenshot -> identify the transaction from the customer's own ledger
history -> open (or report on) a case. Customer-supplied amounts are only used to *find* a transaction;
the case always runs on the ledger's verified amount."""
from __future__ import annotations

import re
import uuid

import claim_verifier
import clock
import ledger_db
import merchant_rails
import orchestrator as orc
from mock_ledger import bank
from untrusted_ingestion import ClaimedContext, extract_amounts, scan_for_injection, strip_refs

ISSUE_RE = re.compile(r"refund|rfnd|pais|payment|pmt|\bkat|cut|debit|fail|fld|wapas|txn|transaction|upi|money|amount|"
                      r"merchant|dukaan|double|dobara|do baar|credit|nahi aa|nhi a|ni aa|atak|अटक|reversal|₹|\brs\b|theek|"
                      r"cancel|radd|deliver|order|recharge|train|ticket|pnr|booking|return|khana|chargeback", re.I)
STATUS_RE = re.compile(r"status|\bkab\b|kahan|update|kya hua|\bwhen\b|\bwhere\b|kitna time|kitne din", re.I)


# Spoken Hinglish numbers (voice transcripts arrive as words: "teen sau pachaas", "ek chaar nau nau").
DIGIT_WORDS = {"shunya": 0, "zero": 0, "ek": 1, "do": 2, "teen": 3, "chaar": 4, "char": 4, "paanch": 5, "panch": 5,
               "chhe": 6, "chhah": 6, "che": 6, "saat": 7, "aath": 8, "nau": 9}
NUM_WORDS = {**DIGIT_WORDS, "das": 10, "gyarah": 11, "barah": 12, "baarah": 12, "terah": 13, "chaudah": 14, "pandrah": 15,
             "solah": 16, "satrah": 17, "atharah": 18, "unnis": 19, "bees": 20, "pachees": 25, "pachchees": 25, "tees": 30,
             "chalees": 40, "chaalis": 40, "pachaas": 50, "pachas": 50, "saath": 60, "sattar": 70, "assi": 80, "nabbe": 90,
             "ninyanve": 99, "nabbe": 90}
MULT = {"sau": 100, "hazaar": 1000, "hazar": 1000, "lakh": 100000}


def spoken_to_digits(text: str) -> str:
    """Replace runs of Hinglish number words with digits. Only rewrites unambiguous runs: compounds with sau/hazaar/lakh,
    or 3+ consecutive digit words, so everyday words like 'do' (give) and 'saath' (with) are left alone."""
    toks = re.findall(r"\S+", text)
    out, i = [], 0
    clean = lambda t: re.sub(r"[^a-z]", "", t.lower())
    while i < len(toks):
        j = i
        while j < len(toks) and (clean(toks[j]) in NUM_WORDS or clean(toks[j]) in MULT):
            j += 1
        run = [clean(t) for t in toks[i:j]]
        if run and any(r in MULT for r in run) and run[0] not in MULT or (run and run[0] in MULT and len(run) > 1):
            total = cur = 0
            for r in run:
                if r in MULT:
                    if MULT[r] >= 1000:
                        total += (cur or 1) * MULT[r]; cur = 0
                    else:
                        cur = (cur or 1) * MULT[r]
                else:
                    cur += NUM_WORDS[r]
            out.append(str(total + cur)); i = j
        elif len(run) >= 3 and all(r in DIGIT_WORDS for r in run):
            out.append("".join(str(DIGIT_WORDS[r]) for r in run)); i = j
        else:
            out.append(toks[i]); i += 1
    return " ".join(out)


def _msg(conv: dict, sender: str, text: str, **kw) -> None:
    conv["messages"].append({"id": uuid.uuid4().hex, "from": sender, "text": text, "at": clock.now().isoformat(), **kw})


def _txn_chip(t: dict) -> dict:
    return {"label": f"₹{t['verified_amount']:,.0f} · {t['counterparty']}", "value": {"type": "txn", "txn_id": t["txn_id"]}}


def _open(conv: dict, cid: str, txn_id: str, text: str, voice_mode: str | None, attachment: dict | None,
          refs: list[str] | None = None) -> str:
    existing = ledger_db.load_case(txn_id)
    if existing:
        c = orc.view_case(txn_id)
        _msg(conv, "vaada", orc.status_text(c), case_id=txn_id)
        ledger_db.save_conversation(conv)
        return txn_id
    conv["pending"] = None
    ledger_db.save_conversation(conv)                 # open_case appends its own messages
    ocr = (attachment or {}).get("ocr")
    orc.open_case(cid, txn_id, "" if voice_mode else text, voice_transcript=text if voice_mode else "",
                  voice_mode=voice_mode, ocr=ocr if ocr and ocr.get("amount") is not None else None, attachment=attachment,
                  refs=refs or [])
    return txn_id


def post_message(cid: str, text: str, *, choice: dict | None = None, voice_mode: str | None = None,
                 attachment: dict | None = None) -> dict:
    text = (text or "").strip()
    if voice_mode:
        text = spoken_to_digits(text)
    with orc.LOCK:
        conv = orc.load_conversation(cid)
        _msg(conv, "customer", text, voice=bool(voice_mode), voice_mode=voice_mode, attachment=attachment)
        case_id = None

        if choice and choice.get("type") == "txn" and conv.get("pending"):
            p = conv["pending"]
            case_id = _open(conv, cid, choice["txn_id"], p["text"], p.get("voice_mode"), p.get("attachment") or attachment,
                            p.get("refs"))
            return {"conversation": orc.load_conversation(cid), "case_id": case_id}

        txns = bank.list_for_customer(cid)
        cases = {c["txn_id"]: c for c in orc.customer_cases(cid)}
        open_cases = [c for c in cases.values() if c["state"] != "VERIFIED_CLOSED" and not c.get("outcome")]

        # A screenshot alone is enough when OCR could read it; only ask what's wrong when it couldn't.
        readable = ((attachment or {}).get("ocr") or {}).get("amount") is not None
        if attachment and not text and not readable:
            if open_cases:
                c = open_cases[0]
                stored = ledger_db.load_case(c["txn_id"])
                stored.setdefault("attachments", []).append(attachment)
                ledger_db.save_case(stored)
                _msg(conv, "vaada", "Screenshot case me jod diya hai. (Hum amount hamesha bank records se hi verify karte hain.)",
                     case_id=c["txn_id"])
            else:
                conv["pending"] = {"text": "", "attachment": attachment}
                _msg(conv, "vaada", "Screenshot mil gaya. Problem kya hai, ek line me bata dijiye?")
            ledger_db.save_conversation(conv)
            return {"conversation": conv, "case_id": None}

        pend_att = (conv.get("pending") or {}).get("attachment")
        attachment = attachment or pend_att
        # References the customer quoted (PNR / order ID / payment RRN). They tell us where to look; the claim verifier
        # later checks each one against the records, so quoting someone else's booking is caught, not trusted.
        shot_text = ((attachment or {}).get("ocr") or {}).get("embedded_text") or ""   # a screenshot is just another claim
        order_refs = merchant_rails.refs_in_text(f"{text} {shot_text}")
        rrns = claim_verifier.rrns_in_text(f"{text} {shot_text}")
        refs = [r["ref"] for r in order_refs] + rrns
        amounts = extract_amounts(strip_refs(text, refs))
        ocr_amt = ((attachment or {}).get("ocr") or {}).get("amount")
        if not amounts and ocr_amt:
            amounts = [float(ocr_amt)]      # only used to *find* a transaction; the ledger amount is what counts

        # 1. prompt injection: contain immediately, attach to the most relevant txn for the audit trail
        if scan_for_injection(ClaimedContext(raw_text=text))["adversarial"]:
            contained = [c for c in cases.values() if c.get("outcome") == "CONTAINED"]
            if contained:
                _msg(conv, "vaada", orc.status_text(contained[0]), case_id=contained[0]["txn_id"])
                ledger_db.save_conversation(conv)
                return {"conversation": conv, "case_id": contained[0]["txn_id"]}
            target = next((t for t in txns if t["verified_amount"] in amounts), txns[0])
            case_id = _open(conv, cid, target["txn_id"], text, voice_mode, attachment, refs)
            return {"conversation": orc.load_conversation(cid), "case_id": case_id}

        # 2. a quoted reference that points at one of the customer's own payments is an exact match
        mine = {t["txn_id"]: t for t in txns}
        exact = [r["payment_txn_id"] for r in order_refs if r["payment_txn_id"] in mine]
        exact += [t["txn_id"] for t in txns if t.get("rrn") in rrns]
        if exact:
            case_id = _open(conv, cid, exact[0], text, voice_mode, attachment, refs)
            return {"conversation": orc.load_conversation(cid), "case_id": case_id}
        if refs:
            # Quoted a reference that isn't theirs (or doesn't exist): open on the customer's own payment that best fits
            # (same amount, else same merchant) so the verifier can record the contradiction on the audit trail.
            foreign_merchants = {r["merchant_id"] for r in order_refs}
            fit = [t for t in txns if any(abs(t["verified_amount"] - a) < 0.5 for a in amounts)] or \
                  [t for t in txns if t.get("merchant_id") in foreign_merchants]
            if len(fit) == 1:
                case_id = _open(conv, cid, fit[0]["txn_id"], text, voice_mode, attachment, refs)
                return {"conversation": orc.load_conversation(cid), "case_id": case_id}

        # 3. amount mentioned -> match against the customer's own transactions
        if amounts:
            match = [t for t in txns if any(abs(t["verified_amount"] - a) < 0.5 for a in amounts)]
            if len(match) == 1:
                case_id = _open(conv, cid, match[0]["txn_id"], text, voice_mode, attachment, refs)
                return {"conversation": orc.load_conversation(cid), "case_id": case_id}
            conv["pending"] = {"text": text, "voice_mode": voice_mode, "attachment": attachment, "refs": refs}
            lead = (f"Mujhe aapke account me ₹{amounts[0]:,.0f} ka koi transaction nahi mila." if not match
                    else "Is amount ke ek se zyada transactions hain.")
            _msg(conv, "vaada", f"{lead} Aap kis payment ki baat kar rahe hain?", chips=[_txn_chip(t) for t in (match or txns)])
            ledger_db.save_conversation(conv)
            return {"conversation": conv, "case_id": None}

        # 4. follow-up on an open case
        if open_cases and (STATUS_RE.search(text) or not ISSUE_RE.search(text)):
            c = open_cases[0]
            _msg(conv, "vaada", orc.status_text(c), case_id=c["txn_id"])
            ledger_db.save_conversation(conv)
            return {"conversation": conv, "case_id": c["txn_id"]}

        # 5. small talk
        if not ISSUE_RE.search(text):
            _msg(conv, "vaada", "Main UPI payments aur refunds me madad karta hoon. Bataiye kya hua — jaise "
                                "\"mera 350 ka refund nahi aaya\".")
            ledger_db.save_conversation(conv)
            return {"conversation": conv, "case_id": None}

        # 6. issue without amount -> a single obvious candidate; otherwise ask. "Cancelled / not delivered / recharge
        #    failed" points at successful merchant payments; "money cut, refund not received" at failed payments.
        if claim_verifier.claimed_reason(text):
            problem = [t for t in txns if t["tx_type"] == "P2M" and t["failure_stage"] == "NONE" and t["txn_id"] not in cases]
        else:
            problem = [t for t in txns if t["failure_stage"] != "NONE" and t["txn_id"] not in cases]
        if len(problem) == 1:
            case_id = _open(conv, cid, problem[0]["txn_id"], text, voice_mode, attachment, refs)
            return {"conversation": orc.load_conversation(cid), "case_id": case_id}
        conv["pending"] = {"text": text, "voice_mode": voice_mode, "attachment": attachment, "refs": refs}
        _msg(conv, "vaada", "Kaunsa payment? Neeche se chuniye:", chips=[_txn_chip(t) for t in (problem or txns)])
        ledger_db.save_conversation(conv)
        return {"conversation": conv, "case_id": None}
