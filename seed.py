"""Seed the inventory database with realistic metal-finishing demo data.

Running this script drops and recreates the database, then loads ~12 chemicals,
3 process tanks, two weeks of intake/usage/maintenance activity, chemistry
readings, permit limits, and effluent samples (including one over its limit so
the dashboard's red flag is visible).
"""
import os
import random
from datetime import date, timedelta

import db

random.seed(42)
TODAY = date(2026, 6, 22)


def d(days_ago):
    """Return an ISO date string `days_ago` days before TODAY."""
    return (TODAY - timedelta(days=days_ago)).isoformat()


# (name, CAS, hazard class, SDS link, unit, reorder_level)
CHEMICALS = [
    ("Nickel Sulfate Hexahydrate", "10101-97-0", "Carcinogen / Aquatic Toxic",
     "https://sds.example.com/nickel-sulfate", "kg", 50),
    ("Nickel Chloride", "7791-20-0", "Carcinogen / Toxic",
     "https://sds.example.com/nickel-chloride", "kg", 25),
    ("Chromic Acid (Chromium Trioxide)", "1333-82-0", "Oxidizer / Carcinogen",
     "https://sds.example.com/chromic-acid", "kg", 40),
    ("Sodium Hydroxide", "1310-73-2", "Corrosive",
     "https://sds.example.com/sodium-hydroxide", "kg", 60),
    ("Sulfuric Acid 98%", "7664-93-9", "Corrosive",
     "https://sds.example.com/sulfuric-acid", "L", 80),
    ("Hydrochloric Acid 32%", "7647-01-0", "Corrosive",
     "https://sds.example.com/hydrochloric-acid", "L", 70),
    ("Boric Acid", "10043-35-3", "Reproductive Toxin",
     "https://sds.example.com/boric-acid", "kg", 30),
    ("Copper Sulfate Pentahydrate", "7758-99-8", "Aquatic Toxic / Irritant",
     "https://sds.example.com/copper-sulfate", "kg", 35),
    ("Zinc Chloride", "7646-85-7", "Corrosive / Aquatic Toxic",
     "https://sds.example.com/zinc-chloride", "kg", 30),
    ("Sodium Cyanide", "143-33-9", "Acute Toxic",
     "https://sds.example.com/sodium-cyanide", "kg", 20),
    ("Citric Acid", "77-92-9", "Irritant",
     "https://sds.example.com/citric-acid", "kg", 25),
    ("Nitric Acid 68%", "7697-37-2", "Oxidizer / Corrosive",
     "https://sds.example.com/nitric-acid", "L", 45),
]

TANKS = [
    ("Watts Nickel Plating Tank", "Electroplating - Nickel", 1500, "Line A - Bay 1"),
    ("Hard Chrome Tank", "Electroplating - Chromium", 1200, "Line A - Bay 2"),
    ("Alkaline Soak Cleaner", "Cleaning - Alkaline", 800, "Line B - Pretreat"),
]

# parameter -> permit max (mg/L)
PERMIT_LIMITS = [
    ("Nickel", 2.0),
    ("Total Chromium", 1.5),
    ("Hexavalent Chromium", 0.25),
    ("Copper", 2.5),
    ("Zinc", 2.0),
    ("Cyanide", 0.65),
    ("pH (max)", 9.0),
]


def main():
    # Fresh start each seed run.
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()
    conn = db.get_connection()
    cur = conn.cursor()

    # --- Chemicals ---
    chem_ids = {}
    for name, cas, hazard, sds, unit, reorder in CHEMICALS:
        cur.execute(
            "INSERT INTO chemicals (name, cas_number, hazard_class, sds_link, unit, reorder_level)"
            " VALUES (?,?,?,?,?,?)",
            (name, cas, hazard, sds, unit, reorder),
        )
        chem_ids[name] = cur.lastrowid

    # --- Tanks ---
    tank_ids = []
    for name, ptype, vol, loc in TANKS:
        cur.execute(
            "INSERT INTO tanks (name, process_type, volume_l, location) VALUES (?,?,?,?)",
            (name, ptype, vol, loc),
        )
        tank_ids.append(cur.lastrowid)

    # --- Permit limits ---
    for param, mx in PERMIT_LIMITS:
        cur.execute(
            "INSERT INTO permit_limits (parameter, max_value, unit) VALUES (?,?,?)",
            (param, mx, "pH" if param.startswith("pH") else "mg/L"),
        )

    # --- Lots + intake transactions (deliveries over the last two weeks) ---
    # (chemical name, lot#, received_days_ago, expiry_days_from_today, qty, supplier)
    lot_plan = [
        ("Nickel Sulfate Hexahydrate", "NS-2406-A", 14, 540, 200, "Atlas Chemical Co"),
        ("Nickel Sulfate Hexahydrate", "NS-2406-B", 3, 560, 100, "Atlas Chemical Co"),
        ("Nickel Chloride", "NC-2405-A", 12, 400, 80, "Atlas Chemical Co"),
        ("Chromic Acid (Chromium Trioxide)", "CR-2406-A", 10, 720, 150, "Midwest Plating Supply"),
        ("Sodium Hydroxide", "SH-2406-A", 9, 900, 250, "Brenntag"),
        ("Sulfuric Acid 98%", "SA-2406-A", 13, 1080, 300, "Brenntag"),
        ("Hydrochloric Acid 32%", "HC-2405-A", 11, 720, 220, "Brenntag"),
        ("Boric Acid", "BA-2406-A", 8, 1000, 120, "Midwest Plating Supply"),
        ("Copper Sulfate Pentahydrate", "CS-2406-A", 7, 800, 90, "Atlas Chemical Co"),
        ("Zinc Chloride", "ZC-2406-A", 6, 700, 70, "Midwest Plating Supply"),
        # Sodium cyanide intentionally low to trigger a low-stock alert.
        ("Sodium Cyanide", "CN-2405-A", 13, 365, 22, "Specialty Salts Inc"),
        ("Citric Acid", "CA-2406-A", 5, 600, 100, "Brenntag"),
        # Nitric acid lot expiring soon to trigger an expiring alert.
        ("Nitric Acid 68%", "NA-2503-A", 12, 12, 90, "Brenntag"),
    ]
    lot_ids = {}
    for cname, lotno, recv, exp, qty, supplier in lot_plan:
        cur.execute(
            "INSERT INTO lots (chemical_id, lot_number, received_date, expiry_date, quantity, supplier)"
            " VALUES (?,?,?,?,?,?)",
            (chem_ids[cname], lotno, d(recv), d(-exp), qty, supplier),
        )
        lid = cur.lastrowid
        lot_ids[lotno] = lid
        # Matching intake transaction on the received date.
        cur.execute(
            "INSERT INTO transactions (lot_id, txn_date, txn_type, quantity, note)"
            " VALUES (?,?,?,?,?)",
            (lid, d(recv), "intake", qty, f"Delivery received - {supplier}"),
        )

    # --- Usage transactions spread across two weeks (stock-down) ---
    usage_plan = [
        ("NS-2406-A", [(11, 18), (8, 15), (5, 20), (2, 12)]),
        ("NC-2405-A", [(9, 10), (4, 8)]),
        ("CR-2406-A", [(9, 14), (6, 16), (3, 11)]),
        ("SH-2406-A", [(8, 30), (5, 25), (1, 20)]),
        ("SA-2406-A", [(12, 25), (7, 30), (3, 22)]),
        ("HC-2405-A", [(10, 20), (4, 18)]),
        ("BA-2406-A", [(7, 9), (2, 7)]),
        ("CS-2406-A", [(6, 12), (1, 10)]),
        ("CN-2405-A", [(10, 2), (5, 1)]),  # keeps cyanide under reorder level
    ]
    for lotno, draws in usage_plan:
        for days_ago, qty in draws:
            cur.execute(
                "INSERT INTO transactions (lot_id, txn_date, txn_type, quantity, note)"
                " VALUES (?,?,?,?,?)",
                (lot_ids[lotno], d(days_ago), "usage", qty, "Bath addition / process use"),
            )

    # --- Chemistry readings: a series per tank over two weeks ---
    # Watts Nickel: pH ~3.8-4.6, 50-60C, ~280 g/L
    for days_ago in [13, 11, 9, 7, 5, 3, 1]:
        cur.execute(
            "INSERT INTO readings (tank_id, reading_date, ph, temperature_c, concentration, note)"
            " VALUES (?,?,?,?,?,?)",
            (tank_ids[0], d(days_ago), round(random.uniform(3.8, 4.6), 2),
             round(random.uniform(50, 60), 1), round(random.uniform(270, 300), 1),
             "Routine check"),
        )
    # Hard Chrome: very acidic, 45-55C, ~250 g/L CrO3
    for days_ago in [12, 10, 8, 6, 4, 2]:
        cur.execute(
            "INSERT INTO readings (tank_id, reading_date, ph, temperature_c, concentration, note)"
            " VALUES (?,?,?,?,?,?)",
            (tank_ids[1], d(days_ago), round(random.uniform(0.1, 0.6), 2),
             round(random.uniform(45, 55), 1), round(random.uniform(240, 260), 1),
             "Routine check"),
        )
    # Alkaline soak: pH ~12-13, 60-70C, ~60 g/L
    for days_ago in [13, 10, 7, 4, 1]:
        cur.execute(
            "INSERT INTO readings (tank_id, reading_date, ph, temperature_c, concentration, note)"
            " VALUES (?,?,?,?,?,?)",
            (tank_ids[2], d(days_ago), round(random.uniform(12.0, 13.2), 2),
             round(random.uniform(60, 70), 1), round(random.uniform(50, 70), 1),
             "Routine check"),
        )

    # --- Bath maintenance events ---
    maint_plan = [
        (tank_ids[0], 11, "addition", "Nickel Sulfate Hexahydrate", 18, "Bring up nickel concentration"),
        (tank_ids[0], 5, "addition", "Boric Acid", 5, "Buffer adjustment"),
        (tank_ids[1], 9, "addition", "Chromic Acid (Chromium Trioxide)", 14, "Replenish CrO3"),
        (tank_ids[1], 3, "cleaning", None, None, "Anode cleaning + filtration"),
        (tank_ids[2], 8, "addition", "Sodium Hydroxide", 30, "Caustic boost"),
        (tank_ids[2], 1, "dump", None, None, "Spent bath dumped to treatment"),
    ]
    for tid, days_ago, etype, cname, qty, note in maint_plan:
        cid = chem_ids[cname] if cname else None
        cur.execute(
            "INSERT INTO maintenance (tank_id, event_date, event_type, chemical_id, quantity, note)"
            " VALUES (?,?,?,?,?,?)",
            (tid, d(days_ago), etype, cid, qty, note),
        )

    # --- Effluent samples vs permit limits ---
    # Most compliant; one Hexavalent Chromium sample is over its 0.25 mg/L limit.
    effluent_plan = [
        (12, "Outfall 001", "Nickel", 0.8),
        (12, "Outfall 001", "Total Chromium", 0.4),
        (12, "Outfall 001", "Copper", 0.6),
        (12, "Outfall 001", "pH (max)", 7.8),
        (8, "Outfall 001", "Nickel", 1.1),
        (8, "Outfall 001", "Zinc", 0.9),
        (8, "Outfall 001", "Cyanide", 0.10),
        (4, "Outfall 001", "Nickel", 1.4),
        (4, "Outfall 001", "Total Chromium", 0.7),
        # OVER LIMIT: hexavalent chromium 0.41 > 0.25 -> red flag on dashboard.
        (2, "Outfall 001", "Hexavalent Chromium", 0.41),
        (2, "Outfall 001", "Copper", 1.2),
    ]
    for days_ago, loc, param, val in effluent_plan:
        unit = "pH" if param.startswith("pH") else "mg/L"
        cur.execute(
            "INSERT INTO effluent_samples (sample_date, location, parameter, value, unit, note)"
            " VALUES (?,?,?,?,?,?)",
            (d(days_ago), loc, param, val, unit, "Composite sample"),
        )

    conn.commit()
    conn.close()
    print(f"Seeded demo data into {db.DB_PATH}")


if __name__ == "__main__":
    main()
