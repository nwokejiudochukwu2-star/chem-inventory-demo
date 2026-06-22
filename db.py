"""SQLite database layer for the Metal Finishing Chemical Inventory tracker.

Holds the connection helper and the schema definition. Keeping the schema in
one place makes it easy for the Streamlit app and the seed script to share it.
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory.db")

SCHEMA = """
PRAGMA foreign_keys = ON;

-- Catalog of chemicals we buy and use.
CREATE TABLE IF NOT EXISTS chemicals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    cas_number    TEXT,
    hazard_class  TEXT,
    sds_link      TEXT,
    unit          TEXT    NOT NULL DEFAULT 'kg',
    reorder_level REAL    NOT NULL DEFAULT 0
);

-- Physical lots/containers received into stock. Each lot belongs to a chemical.
CREATE TABLE IF NOT EXISTS lots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chemical_id   INTEGER NOT NULL REFERENCES chemicals(id),
    lot_number    TEXT,
    received_date TEXT    NOT NULL,
    expiry_date   TEXT,
    quantity      REAL    NOT NULL DEFAULT 0,   -- initial received quantity
    supplier      TEXT
);

-- Intake (delivery) and usage (consumption) movements against lots.
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id      INTEGER NOT NULL REFERENCES lots(id),
    txn_date    TEXT    NOT NULL,
    txn_type    TEXT    NOT NULL,               -- 'intake' or 'usage'
    quantity    REAL    NOT NULL,               -- positive number
    note        TEXT
);

-- Process tanks / baths on the finishing line.
CREATE TABLE IF NOT EXISTS tanks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    process_type TEXT,
    volume_l     REAL,
    location     TEXT
);

-- Chemistry readings taken from a tank.
CREATE TABLE IF NOT EXISTS readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tank_id       INTEGER NOT NULL REFERENCES tanks(id),
    reading_date  TEXT    NOT NULL,
    ph            REAL,
    temperature_c REAL,
    concentration REAL,                          -- g/L of primary constituent
    note          TEXT
);

-- Bath maintenance events: additions, dumps, cleanings.
CREATE TABLE IF NOT EXISTS maintenance (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tank_id       INTEGER NOT NULL REFERENCES tanks(id),
    event_date    TEXT    NOT NULL,
    event_type    TEXT    NOT NULL,              -- 'addition', 'dump', 'cleaning'
    chemical_id   INTEGER REFERENCES chemicals(id),
    quantity      REAL,                          -- amount added (drives stock-down)
    note          TEXT
);

-- Permit limits for wastewater parameters (mg/L).
CREATE TABLE IF NOT EXISTS permit_limits (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    parameter TEXT    NOT NULL,
    max_value REAL    NOT NULL,
    unit      TEXT    NOT NULL DEFAULT 'mg/L'
);

-- Effluent (wastewater) samples, one row per parameter measured.
CREATE TABLE IF NOT EXISTS effluent_samples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_date  TEXT    NOT NULL,
    location     TEXT,
    parameter    TEXT    NOT NULL,
    value        REAL    NOT NULL,
    unit         TEXT    NOT NULL DEFAULT 'mg/L',
    note         TEXT
);
"""


def get_connection():
    """Return a SQLite connection with row access by column name."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create all tables if they do not already exist."""
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def lot_on_hand(conn, lot_id):
    """Current quantity on hand for a lot = intakes - usages."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN txn_type = 'intake' THEN quantity
                                 ELSE -quantity END), 0) AS on_hand
        FROM transactions WHERE lot_id = ?
        """,
        (lot_id,),
    ).fetchone()
    return row["on_hand"] if row else 0


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DB_PATH}")
