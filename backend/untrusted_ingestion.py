"""Zero-trust ingestion: customer-supplied content is *claims*; the ledger is *facts*."""
import re
from typing import Optional

from pydantic import BaseModel, Field

AMOUNT_RE = re.compile(r"(?:₹|rs\.?|inr)?\s*(?<![\d-])(\d{1,3}(?:,\d{2,3})+|\d+(?:\.\d+)?)\s*(k\b)?", re.I)
UNIT_AFTER = re.compile(r"^\s*(din|day|days|ghant|hour|hr|hrs|h\b|minute|min|baar|bar|x\b|dafa)", re.I)


def extract_amounts(text: str) -> list[float]:
    """Rupee amounts in free text. Skips references (UTRs, PNRs, phone numbers, FN-1234) and durations ("6 din")."""
    out = []
    for m in AMOUNT_RE.finditer(text or ""):
        digits = m.group(1).replace(",", "")
        if len(digits.split(".")[0]) > 7:
            continue
        if UNIT_AFTER.match(text[m.end():]) and not m.group(2):
            continue
        v = float(digits) * (1000 if m.group(2) else 1)
        if v >= 10:
            out.append(v)
    return out


def strip_refs(text: str, refs: list[str]) -> str:
    for r in refs:
        text = re.sub(re.escape(r).replace(r"\-", "-?"), " ", text, flags=re.I)
    return text


INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) instructions", r"system\s*override", r"ignore\s+policy[_ ]?gate",
    r"\broute\s*=\s*autonomous", r"disable (the )?kill ?switch", r"developer mode", r"###\s*system",
    r"forget your rules", r"wallet cap\s*=", r"without verification", r"\[admin\]",
]


class OcrClaim(BaseModel):
    amount: Optional[float] = None
    utr: Optional[str] = None
    status: Optional[str] = None
    embedded_text: str = ""
    mode: str = "UNKNOWN"


class ClaimedContext(BaseModel):
    """Everything here is attacker-controllable. Never feeds tool params or policy amounts."""
    untrusted: bool = Field(default=True, frozen=True)
    raw_text: str = ""
    voice_transcript: str = ""
    voice_mode: Optional[str] = None
    ocr: Optional[OcrClaim] = None
    refs: list[str] = []             # references the customer quoted (PNR, order ID, payment RRN): where to look, not proof

    @property
    def combined_text(self) -> str:
        parts = [self.raw_text, self.voice_transcript, self.ocr.embedded_text if self.ocr else ""]
        return " . ".join(p for p in parts if p)


class VerifiedContext(BaseModel):
    """Authoritative record from the ledger. The ONLY source of amounts for tools/policy."""
    trusted: bool = Field(default=True, frozen=True)
    txn_id: str
    tx_type: str
    verified_amount: float
    txn_timestamp: str
    gateway_status: str
    bank_status: str
    merchant_status: str
    bank_webhook_received: bool
    user_dispute_history_count: int
    failure_stage: str
    customer_id: str


class ToolParams(BaseModel):
    """Parameters allowed to reach tools / policy gate. Constructible only from VerifiedContext."""
    txn_id: str
    amount_inr: float

    @classmethod
    def from_verified(cls, v: "VerifiedContext") -> "ToolParams":
        if not isinstance(v, VerifiedContext) or not v.trusted:
            raise TypeError("ToolParams may only be built from a VerifiedContext (claims are untrusted)")
        return cls(txn_id=v.txn_id, amount_inr=v.verified_amount)


def verified_from_row(row: dict) -> VerifiedContext:
    return VerifiedContext(
        txn_id=row["txn_id"], tx_type=row["tx_type"], verified_amount=row["verified_amount"],
        txn_timestamp=row["txn_timestamp"], gateway_status=row["gateway_status"], bank_status=row["bank_status"],
        merchant_status=row["merchant_status"], bank_webhook_received=bool(row["bank_webhook_received"]),
        user_dispute_history_count=row["dispute_history_count"], failure_stage=row["failure_stage"],
        customer_id=row["customer_id"])


def scan_for_injection(claim: ClaimedContext) -> dict:
    text = claim.combined_text.lower()
    hits = [p for p in INJECTION_PATTERNS if re.search(p, text)]
    return {"adversarial": bool(hits), "patterns": hits,
            "code": "ADVERSARIAL_INPUT_CONTAINED" if hits else None}


def claim_vs_fact(claim: ClaimedContext, v: VerifiedContext) -> dict:
    """Compare claimed screenshot/text against ledger. Any contradiction => claim_ledger_mismatch."""
    mism = []
    if claim.ocr:
        if claim.ocr.amount is not None and abs(claim.ocr.amount - v.verified_amount) > 0.5:
            mism.append({"field": "amount", "claimed": claim.ocr.amount, "ledger": v.verified_amount})
        if claim.ocr.status and claim.ocr.status.upper() == "FAILED" and v.gateway_status == "SUCCESS" \
                and v.bank_status.startswith("CREDITED_TO_BENEFICIARY"):
            mism.append({"field": "status", "claimed": claim.ocr.status, "ledger": v.bank_status})
    amts = extract_amounts(strip_refs(claim.raw_text, claim.refs))
    if amts and claim.ocr is None:
        amt = amts[0]
        if abs(amt - v.verified_amount) > 0.5 and amt >= 100:
            mism.append({"field": "text_amount", "claimed": amt, "ledger": v.verified_amount})
    return {"claim_ledger_mismatch": bool(mism), "mismatches": mism,
            "code": "SCREENSHOT_LEDGER_MISMATCH" if mism else None}


def multi_system_discrepancy(v: VerifiedContext) -> dict:
    """Gateway / bank switch / merchant ledger disagree about the same transaction."""
    findings = []
    if v.gateway_status == "FAILED" and v.bank_status.startswith("DEBITED"):
        findings.append("GATEWAY_FAILED_BUT_BANK_DEBITED")
    if v.gateway_status == "SUCCESS" and v.merchant_status in ("NOT_RECEIVED", "PENDING") and v.bank_status.startswith("DEBITED"):
        findings.append("GATEWAY_SUCCESS_BUT_MERCHANT_NOT_CONFIRMED")
    return {"discrepancy": bool(findings), "findings": findings}
