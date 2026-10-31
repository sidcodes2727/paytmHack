"""Durable app state (SQLModel on SQLite/WAL): cases, hash-chained promise ledger,
idempotency locks and agent-wallet spend."""
import json
from datetime import datetime
from typing import Optional

from sqlalchemy import event
from sqlmodel import Field, Session, SQLModel, create_engine, select

import clock
import security
from config import DB_PATH

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False, "timeout": 15})


@event.listens_for(engine, "connect")
def _pragmas(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


class CaseRow(SQLModel, table=True):
    txn_id: str = Field(primary_key=True)
    state: str = "OPEN_TRIAGED"
    body: str = "{}"          # JSON document
    updated_at: str = ""


class ConversationRow(SQLModel, table=True):
    customer_id: str = Field(primary_key=True)
    body: str = "{}"


class ChainEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    txn_id: str = Field(index=True)
    seq: int
    event: str
    from_state: str
    to_state: str
    payload: str
    timestamp: str
    prev_hash: str
    current_hash: str


class IdempotencyLock(SQLModel, table=True):
    key: str = Field(primary_key=True)     # sha256(txn_id:action_type)
    txn_id: str
    action_type: str
    created_at: str


class WalletSpend(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    txn_id: str
    amount: float
    ts: str


STATES = ["OPEN_TRIAGED", "PROMISE_ACTIVE", "AWAITING_APPROVAL", "ACTION_EXECUTED",
          "AWAITING_PROOF", "VERIFIED_CLOSED"]
GENESIS = "0" * 64


def init_db(reset: bool = False) -> None:
    if reset:
        SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


def session() -> Session:
    return Session(engine)


# ---- cases -----------------------------------------------------------------
def save_case(case: dict) -> None:
    with session() as s:
        row = s.get(CaseRow, case["txn_id"]) or CaseRow(txn_id=case["txn_id"])
        row.state = case["state"]
        row.body = json.dumps(case, default=str)
        row.updated_at = clock.now().isoformat()
        s.add(row)
        s.commit()


def load_case(txn_id: str) -> dict | None:
    with session() as s:
        row = s.get(CaseRow, txn_id)
        return json.loads(row.body) if row else None


def list_case_ids() -> list[str]:
    with session() as s:
        return [r.txn_id for r in s.exec(select(CaseRow).order_by(CaseRow.txn_id)).all()]


# ---- conversations -----------------------------------------------------------
def save_conversation(conv: dict) -> None:
    with session() as s:
        row = s.get(ConversationRow, conv["customer_id"]) or ConversationRow(customer_id=conv["customer_id"])
        row.body = json.dumps(conv, default=str)
        s.add(row)
        s.commit()


def load_conversation(customer_id: str) -> dict | None:
    with session() as s:
        row = s.get(ConversationRow, customer_id)
        return json.loads(row.body) if row else None


# ---- hash chain --------------------------------------------------------------
def _hash(prev: str, payload: dict, ts: str) -> str:
    return security.sha256_hex(prev + security.canonical(payload) + ts)


def append_chain(txn_id: str, event_name: str, to_state: str, payload: dict, from_state: str | None = None) -> ChainEntry:
    """current_hash = SHA256(prev_hash + canonical_payload + timestamp)"""
    with session() as s:
        last = s.exec(select(ChainEntry).where(ChainEntry.txn_id == txn_id).order_by(ChainEntry.seq.desc())).first()
        prev = last.current_hash if last else GENESIS
        seq = (last.seq + 1) if last else 0
        frm = from_state or (last.to_state if last else "NONE")
        if to_state != frm and frm != "NONE":
            if STATES.index(to_state) < STATES.index(frm):
                raise ValueError(f"illegal transition {frm} -> {to_state}")
        ts = clock.now().isoformat()
        body = {"event": event_name, "from": frm, "to": to_state, "data": payload}
        entry = ChainEntry(txn_id=txn_id, seq=seq, event=event_name, from_state=frm, to_state=to_state,
                           payload=security.canonical(body), timestamp=ts, prev_hash=prev,
                           current_hash=_hash(prev, body, ts))
        s.add(entry)
        s.commit()
        s.refresh(entry)
        return entry


def get_chain(txn_id: str) -> list[dict]:
    with session() as s:
        rows = s.exec(select(ChainEntry).where(ChainEntry.txn_id == txn_id).order_by(ChainEntry.seq)).all()
        return [r.model_dump() for r in rows]


def verify_chain(txn_id: str) -> bool:
    prev = GENESIS
    for e in get_chain(txn_id):
        if e["prev_hash"] != prev or _hash(prev, json.loads(e["payload"]), e["timestamp"]) != e["current_hash"]:
            return False
        prev = e["current_hash"]
    return True


# ---- idempotency + wallet ------------------------------------------------------
def idem_key(txn_id: str, action_type: str) -> str:
    return security.sha256_hex(txn_id + ":" + action_type)


def idem_exists(txn_id: str, action_type: str) -> bool:
    with session() as s:
        return s.get(IdempotencyLock, idem_key(txn_id, action_type)) is not None


def idem_acquire(txn_id: str, action_type: str) -> bool:
    """Atomic: the primary-key insert succeeds for exactly one caller."""
    from sqlalchemy.exc import IntegrityError
    with session() as s:
        s.add(IdempotencyLock(key=idem_key(txn_id, action_type), txn_id=txn_id, action_type=action_type,
                              created_at=clock.now().isoformat()))
        try:
            s.commit()
            return True
        except IntegrityError:
            s.rollback()
            return False


def wallet_spent_last_hour() -> float:
    cutoff = clock.now().timestamp() - 3600
    with session() as s:
        rows = s.exec(select(WalletSpend)).all()
    return sum(r.amount for r in rows if datetime.fromisoformat(r.ts).timestamp() >= cutoff)


def wallet_record(txn_id: str, amount: float) -> None:
    with session() as s:
        s.add(WalletSpend(txn_id=txn_id, amount=amount, ts=clock.now().isoformat()))
        s.commit()
