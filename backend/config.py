"""Central policy constants for VAADA.

These are *policy knobs* (business rules, regulatory constants), not model metrics.
Every learned quantity (tau*, confidences, latencies, F1, ECE ...) is computed at runtime.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE, # comments). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v:
            os.environ.setdefault(k.strip(), v)


_load_env(BASE_DIR / ".env")
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "vaada.db"
ARTIFACT_PATH = DATA_DIR / "nirnay_artifact.joblib"
BENCHMARK_PATH = BASE_DIR / "benchmark_results.json"

# --- Cost-sensitive abstention (asymmetric cost matrix) ---------------------
C_ERROR = 4.0      # false route / false auto-approval
C_ABSTAIN = 1.0    # fall back to slow path
TAU_MIN, TAU_MAX, TAU_STEPS = 0.50, 0.99, 99   # search interval for tau*

# --- Stage 2 specialist squad --------------------------------------------
CREW_TIMEOUT_S = 120          # live CrewAI run is bounded; on timeout the deterministic squad takes over

# --- Agent Wallet ------------------------------------------------------------
AGENT_WALLET_PER_CASE_INR = 500
AGENT_WALLET_HOURLY_INR = 5000

# --- Approval / promise SLAs (hours of virtual time) -------------------------
PRIMARY_APPROVER = "FINANCE_PRIMARY"
BACKUP_APPROVER = "FINANCE_BACKUP_LEAD"
APPROVER_SLA_HOURS = 12.0
CUSTOMER_UPDATE_WINDOW_HOURS = 24.0     # we promise the customer an update within this window
PROACTIVE_UPDATE_REMAINING_FRACTION = 0.25  # nudge customer when <=25% of window is left

# --- RBI Circular RBI/2019-20/67 (DPSS.CO.PD No.629/02.01.014/2019-20) -------
RBI_CIRCULAR_REF = "RBI/2019-20/67 DPSS.CO.PD No.629/02.01.014/2019-20"
RBI_TAT_DAYS = {"P2P": 1, "P2M": 5}
RBI_COMP_PER_DAY_INR = 100

# --- Roles -------------------------------------------------------------------
ROLES = ["role:nirnay_triage", "role:crew_investigator", "role:crew_compiler",
         "role:crew_planner", "role:scoped_executor", "role:independent_auditor"]

# --- Approved proof-check catalog -------------------------------------------
PROOF_CATALOG = {
    "BANK_UTR_CREDITED": "Settled 12-digit UTR with status CREDITED for the refunded amount in bank ledger",
    "GATEWAY_REVERSAL_CONFIRMED": "Gateway reversal record present and matched to a credited bank UTR",
    "MERCHANT_SETTLEMENT_CONFIRMED": "Merchant acquirer settlement confirmation with matching UTR",
    "DUPLICATE_REVERSAL_UTR": "Credited UTR for the duplicate (second) debit amount",
    "MERCHANT_REFUND_UTR_CREDITED": "Merchant refund credited to the original payment source: signed receipt with a settled 12-digit UTR",
}

# --- Refunds owed after a *successful* payment (cancellation, non-delivery, return, failed fulfilment) ---------
# Keyed by merchant category, then "<ORDER_STATUS>:<CANCELLED_BY>" ("*" = anyone). Entitlement:
#   "full"       -> the whole payment is due
#   "refundable" -> the merchant record's own refundable_amount (e.g. after cancellation charges)
# sla_days are sandbox merchant policies, not regulation: in production they come from each merchant's published
# refund policy (RBI's failed-transaction TAT does not apply because the payment itself succeeded).
MERCHANT_REFUND_RULES = {
    "TRAVEL": {"label": "Travel booking", "sla_days": 7,
               "due": {"CANCELLED:MERCHANT": "full", "CANCELLED:SYSTEM": "full", "CANCELLED:CUSTOMER": "refundable"}},
    "FOOD": {"label": "Food order", "sla_days": 2,
             "due": {"CANCELLED:MERCHANT": "full", "CANCELLED:SYSTEM": "full", "NOT_DELIVERED:*": "full",
                     "CANCELLED:CUSTOMER": "refundable"}},
    "ECOMMERCE": {"label": "Online order", "sla_days": 7,
                  "due": {"RETURNED:*": "refundable", "NOT_DELIVERED:*": "full", "CANCELLED:*": "full"}},
    "RECHARGE": {"label": "Recharge / bill payment", "sla_days": 3, "due": {"FAILED_FULFILMENT:*": "full"}},
    "SUBSCRIPTION": {"label": "Subscription", "sla_days": 7, "due": {"CANCELLED:CUSTOMER": "refundable"}},
}
MERCHANT_DISPUTE_SLA_HOURS = 72.0   # no connector: we promise the merchant's answer (dispute reference), not money

# --- Claim history prior -----------------------------------------------------------------------------------------
# Prior disputes (from the bank) + claims VAADA already found contradicted. At or above this, no refund runs without
# a human, even when the records support the claim. It raises the evidence bar; it never rejects a claim by itself.
CLAIM_HISTORY_REVIEW_THRESHOLD = 3
