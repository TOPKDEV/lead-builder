"""One SQLite file per job. Every step writes as it goes, so a stopped run
picks up where it left off and nothing already paid for is bought twice."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    mls_number TEXT PRIMARY KEY,
    market TEXT, address TEXT, city TEXT, state TEXT, zip_code TEXT,
    price REAL, beds INTEGER, baths REAL, sqft INTEGER, days_on_market INTEGER,
    property_type TEXT, listing_url TEXT,
    agent_name TEXT, agent_phone TEXT, agent_brokerage TEXT, agent_email TEXT,
    detail_status TEXT              -- unused since listings come with their agent
);
CREATE TABLE IF NOT EXISTS markets_done (
    label TEXT PRIMARY KEY, listings INTEGER, done_at TEXT
);
CREATE TABLE IF NOT EXISTS bands (
    market TEXT, lo INTEGER, hi INTEGER,  -- hi NULL: no upper limit
    expected INTEGER,               -- Zillow's count, for the quote
    status TEXT,                    -- NULL until searched, then 'done' or 'split'
    PRIMARY KEY (market, lo)
);
CREATE TABLE IF NOT EXISTS agents (
    key TEXT PRIMARY KEY,
    name TEXT, first_name TEXT, last_name TEXT,
    phone TEXT, other_phones TEXT, brokerage TEXT, cities TEXT, markets TEXT,
    active_listings INTEGER, total_volume REAL, median_price REAL, avg_dom REAL,
    sample_urls TEXT,
    email TEXT, email_source TEXT,
    email_status TEXT,              -- 'imported', 'found', 'not_found', 'exhausted'
    email_check TEXT,               -- verifier verdict: ok, catch_all, unknown, invalid, disposable
    phone_e164 TEXT, phone_valid INTEGER, phone_type TEXT, phone_carrier TEXT,
    phone_status TEXT,              -- 'checked', 'invalid_format', 'failed'
    phone_caller_name TEXT, phone_caller_type TEXT,
    phone_name_match TEXT,          -- caller ID vs agent: match, mismatch, unknown
    phone_fix TEXT,                 -- fix_phones: 'replaced', 'confirmed' or 'not_found'
    phone_note TEXT,                -- why the number on file looked wrong
    phone_original TEXT,            -- the number that was replaced
    phone_found_on TEXT,            -- where the replacement was found
    phone_trace TEXT,               -- the skip trace record behind the phone check, as JSON
    homes_phone TEXT,
    phone_litigator INTEGER,        -- 1 if the number belongs to a known TCPA litigator
    homes_profile TEXT, seller_deals_year INTEGER, deals_year INTEGER,
    seller_volume_year REAL, seller_avg_price REAL, closed_sales_5yr INTEGER,
    volume_status TEXT,             -- 'found', 'no_profile', 'unreadable'
    rows_merged INTEGER DEFAULT 1,
    extra TEXT                      -- the imported row's own columns, as JSON
);
CREATE TABLE IF NOT EXISTS usage (
    provider TEXT PRIMARY KEY, calls INTEGER DEFAULT 0, units REAL DEFAULT 0
);
"""

LISTING_COLS = ["mls_number", "market", "address", "city", "state", "zip_code", "price", "beds",
                "baths", "sqft", "days_on_market", "property_type", "listing_url",
                "agent_name", "agent_phone", "agent_brokerage", "agent_email"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Steps save from worker threads, so one connection shared under a lock.
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.conn.executescript(SCHEMA)
        self._add_missing_columns()

    def _add_missing_columns(self):
        """A job started on an older version gains the newer columns."""
        for table in ("listings", "agents"):
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            block = SCHEMA.split(f"CREATE TABLE IF NOT EXISTS {table} (")[1].split(");")[0]
            for line in block.splitlines():
                for part in line.split("--")[0].split(","):
                    words = part.split()
                    if len(words) >= 2 and words[0] not in have and words[0].isidentifier():
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {words[0]} {words[1]}")
        self.conn.commit()

    def close(self):
        self.conn.close()

    def q(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params=()):
        rows = self.q(sql, params)
        return rows[0][0] if rows else None

    def write(self, sql: str, params=()) -> None:
        with self.lock:
            self.conn.execute(sql, params)
            self.conn.commit()

    def write_many(self, sql: str, rows) -> None:
        with self.lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()

    # --- listings ---

    def save_listings(self, listings) -> None:
        # A listing found again (in a band that was split) refreshes price and
        # days on market only.
        self.write_many(f"""
            INSERT INTO listings ({", ".join(LISTING_COLS)})
            VALUES ({", ".join("?" for _ in LISTING_COLS)})
            ON CONFLICT(mls_number) DO UPDATE SET
                price=excluded.price, days_on_market=excluded.days_on_market
        """, [tuple(getattr(l, c) for c in LISTING_COLS) for l in listings])

    def mark_market_done(self, label: str, count: int) -> None:
        self.write("INSERT OR REPLACE INTO markets_done VALUES (?,?,?)", (label, count, now()))

    def market_done(self, label: str) -> bool:
        return self.one("SELECT 1 FROM markets_done WHERE label = ?", (label,)) is not None

    def save_bands(self, market: str, bands) -> None:
        self.write_many("INSERT OR IGNORE INTO bands (market, lo, hi, expected) VALUES (?,?,?,?)",
                        [(market, lo, hi, n) for lo, hi, n in bands])

    # --- agents ---

    def update_agent(self, key: str, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.write(f"UPDATE agents SET {cols} WHERE key = ?", (*fields.values(), key))

    def agents(self, where: str = "1=1", params=()) -> list[dict]:
        return [dict(r) for r in self.q(f"SELECT * FROM agents WHERE {where} "
                                        "ORDER BY active_listings DESC, total_volume DESC", params)]

    # --- spend ---

    def add_usage(self, provider: str, calls: int = 1, units: float = 0) -> None:
        self.write("""INSERT INTO usage (provider, calls, units) VALUES (?,?,?)
                      ON CONFLICT(provider) DO UPDATE SET calls = calls + excluded.calls,
                      units = units + excluded.units""", (provider, calls, units))

    def usage(self) -> dict[str, dict]:
        return {r["provider"]: {"calls": r["calls"], "units": r["units"]}
                for r in self.q("SELECT * FROM usage")}


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)
