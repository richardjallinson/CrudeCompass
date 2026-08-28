"""Crude Compass v1B — storage.

SQLite, one file, no server. Plenty for decades of daily data.

Four tables:
    prices      daily series from FRED, one row per (series, date)
    weekly      weekly series from EIA and CFTC, one row per (series, date)
    predictions the locked calls — the honest record, append-only in spirit
    runs        a log of pipeline runs, for debugging a bad morning
"""

import sqlite3
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    series TEXT NOT NULL,
    date   TEXT NOT NULL,
    value  REAL,
    PRIMARY KEY (series, date)
);

CREATE TABLE IF NOT EXISTS weekly (
    series TEXT NOT NULL,
    date   TEXT NOT NULL,
    value  REAL,
    PRIMARY KEY (series, date)
);

-- The locked calls. `made_at` is when the lock happened; `resolved_*` are
-- filled in later, once the settlement for that date is available.
CREATE TABLE IF NOT EXISTS predictions (
    date            TEXT PRIMARY KEY,   -- the session being predicted
    made_at         TEXT NOT NULL,      -- ISO timestamp of the lock
    state           TEXT NOT NULL,      -- 'up' | 'down' | 'none'
    probability     REAL NOT NULL,
    reference_price REAL,               -- prior settlement the call is measured from
    range_low       REAL,
    range_high      REAL,
    drivers_json    TEXT,
    resolved_price  REAL,               -- settlement for `date`, once known
    outcome         TEXT                -- 'hit' | 'miss' | 'stand' | NULL if open
);

CREATE TABLE IF NOT EXISTS runs (
    started_at TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    note       TEXT
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)


def upsert_series(table, series, rows):
    """rows: iterable of (date_str, value_or_None). Returns count written."""
    if table not in ("prices", "weekly"):
        raise ValueError("table must be prices or weekly")
    payload = [(series, d, v) for d, v in rows]
    with connect() as conn:
        conn.executemany(
            f"INSERT INTO {table} (series, date, value) VALUES (?, ?, ?) "
            f"ON CONFLICT(series, date) DO UPDATE SET value=excluded.value",
            payload,
        )
    return len(payload)


def read_series(table, series):
    with connect() as conn:
        cur = conn.execute(
            f"SELECT date, value FROM {table} WHERE series = ? ORDER BY date",
            (series,),
        )
        return [(r["date"], r["value"]) for r in cur.fetchall()]


def save_prediction(rec):
    with connect() as conn:
        conn.execute(
            """INSERT INTO predictions
               (date, made_at, state, probability, reference_price,
                range_low, range_high, drivers_json)
               VALUES (:date, :made_at, :state, :probability, :reference_price,
                       :range_low, :range_high, :drivers_json)
               ON CONFLICT(date) DO NOTHING""",
            rec,
        )
        # ON CONFLICT DO NOTHING is deliberate. A locked call is locked: if the
        # pipeline runs twice in a morning, the first call stands. Overwriting
        # would quietly let the model shop for a better answer, which is
        # exactly the dishonesty the Scoreboard exists to prevent.


def resolve_predictions(settlements):
    """settlements: dict of date -> settlement price. Fills in outcomes."""
    filled = 0
    with connect() as conn:
        cur = conn.execute(
            "SELECT date, state, reference_price FROM predictions WHERE outcome IS NULL"
        )
        open_rows = cur.fetchall()
        for row in open_rows:
            price = settlements.get(row["date"])
            if price is None or row["reference_price"] is None:
                continue
            if row["state"] == "none":
                outcome = "stand"
            else:
                went_up = price > row["reference_price"]
                outcome = "hit" if (went_up == (row["state"] == "up")) else "miss"
            conn.execute(
                "UPDATE predictions SET resolved_price = ?, outcome = ? WHERE date = ?",
                (price, outcome, row["date"]),
            )
            filled += 1
    return filled


def read_predictions(limit=None):
    q = "SELECT * FROM predictions ORDER BY date DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    with connect() as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]


def log_run(started_at, ok, note=""):
    with connect() as conn:
        conn.execute(
            "INSERT INTO runs (started_at, ok, note) VALUES (?, ?, ?)",
            (started_at, 1 if ok else 0, note),
        )
