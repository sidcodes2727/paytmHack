"""HMAC primitives: capability tokens, approval tokens, bank receipts."""
import hashlib
import hmac
import json
import os
import secrets

from config import ROLES

# Per-process secrets (demo). Override via env for stable tokens across restarts.
_SECRET = (os.environ.get("VAADA_SECRET") or secrets.token_hex(32)).encode()
_BANK_KEY = (os.environ.get("VAADA_BANK_KEY") or secrets.token_hex(32)).encode()


def _sig(key: bytes, msg: str) -> str:
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


def issue_capability(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"unknown role {role}")
    return f"{role}.{_sig(_SECRET, 'cap|' + role)}"


def verify_capability(token: str, required_role: str | None = None) -> str | None:
    """Return the role if the token is authentic (and matches required_role), else None."""
    if not token or "." not in token:
        return None
    role, sig = token.rsplit(".", 1)
    if role not in ROLES or not hmac.compare_digest(sig, _sig(_SECRET, "cap|" + role)):
        return None
    if required_role and role != required_role:
        return None
    return role


def sign_approval(payload: dict) -> str:
    return _sig(_SECRET, "approval|" + canonical(payload))


def verify_approval(payload: dict, sig: str) -> bool:
    return hmac.compare_digest(sign_approval(payload), sig or "")


def sign_bank_receipt(payload: dict) -> str:
    return _sig(_BANK_KEY, "receipt|" + canonical(payload))


def verify_bank_receipt(payload: dict, sig: str) -> bool:
    return hmac.compare_digest(sign_bank_receipt(payload), sig or "")


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()
