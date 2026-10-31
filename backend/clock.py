"""Virtual clock for the demo Time-Machine. Offset is persisted in SQLite."""
import sqlite3
from datetime import datetime, timedelta, timezone

from config import DB_PATH

IST = timezone(timedelta(hours=5, minutes=30))


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS clock_meta (k TEXT PRIMARY KEY, v REAL)")
    return c


def offset_hours() -> float:
    with _conn() as c:
        r = c.execute("SELECT v FROM clock_meta WHERE k='offset_h'").fetchone()
    return float(r[0]) if r else 0.0


def advance(hours: float) -> float:
    if hours < 0:
        raise ValueError("time machine only moves forward")
    new = offset_hours() + hours
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO clock_meta VALUES ('offset_h', ?)", (new,))
    return new


def reset() -> None:
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO clock_meta VALUES ('offset_h', 0)")


def now() -> datetime:
    return datetime.now(IST) + timedelta(hours=offset_hours())
