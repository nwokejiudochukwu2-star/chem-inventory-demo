"""Metal Finishing Chemical Inventory & Compliance Tracker.

A Streamlit demo app over a SQLite database. Run with:

    streamlit run app.py

If the database does not exist yet it is created and seeded automatically.
"""
import os
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import db
import seed

st.set_page_config(page_title="Chem Inventory & Compliance", page_icon="🧪", layout="wide")

EXPIRY_WINDOW_DAYS = 30  # chemicals expiring within this window are flagged


# --------------------------------------------------------------------------- #
# Data helpers
# --------------------------------------------------------------------------- #
def ensure_db():
    """Create and seed the database the first time the app runs."""
    if not os.path.exists(db.DB_PATH):
        seed.main()
    else:
        db.init_db()


@st.cache_data(ttl=2)
def query_df(sql, params=()):
    conn = db.get_connection()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def run_write(sql, params=()):
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
    # Caches must be cleared by the caller after writes.


def chemicals_df():
    return query_df("SELECT * FROM chemicals ORDER BY name")


def permit_limits_map():
    df = query_df("SELECT parameter, max_value, unit FROM permit_limits")
    return {r["parameter"]: (r["max_value"], r["unit"]) for _, r in df.iterrows()}


def inventory_on_hand():
    """Return a DataFrame of current on-hand quantity per chemical."""
    sql = """
        SELECT c.id AS chemical_id, c.name, c.cas_number, c.unit, c.reorder_level,
               COALESCE(SUM(CASE WHEN t.txn_type = 'intake' THEN t.quantity
                                 WHEN t.txn_type = 'usage'  THEN -t.quantity
                                 ELSE 0 END), 0) AS on_hand
        FROM chemicals c
        LEFT JOIN lots l        ON l.chemical_id = c.id
        LEFT JOIN transactions t ON t.lot_id = l.id
        GROUP BY c.id
        ORDER BY c.name
    """
    return query_df(sql)


def today():
    return date(2026, 6, 22)  # fixed "today" so the seeded demo stays consistent


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
def page_dashboard():
    st.title("🧪 Dashboard")
    st.caption("Metal Finishing Chemical Inventory & Compliance overview")

    inv = inventory_on_hand()
    low = inv[inv["on_hand"] <= inv["reorder_level"]]

    lots = query_df("""
        SELECT l.lot_number, c.name AS chemical, l.expiry_date
        FROM lots l JOIN chemicals c ON c.id = l.chemical_id
        WHERE l.expiry_date IS NOT NULL
    """)
    horizon = (today() + timedelta(days=EXPIRY_WINDOW_DAYS)).isoformat()
    expiring = lots[(lots["expiry_date"] <= horizon)].sort_values("expiry_date")

    # Effluent over limit
    eff = query_df("SELECT * FROM effluent_samples ORDER BY sample_date DESC")
    limits = permit_limits_map()
    eff["limit"] = eff["parameter"].map(lambda p: limits.get(p, (None, None))[0])
    eff["over_limit"] = eff.apply(
        lambda r: (r["limit"] is not None) and (r["value"] > r["limit"]), axis=1
    )
    over = eff[eff["over_limit"]]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Chemicals tracked", len(inv))
    c2.metric("Low-stock items", len(low))
    c3.metric("Expiring ≤30d", len(expiring))
    c4.metric("Effluent exceedances", len(over))

    st.divider()

    # Over-limit effluent shown prominently in red
    st.subheader("🚨 Effluent samples over permit limit")
    if over.empty:
        st.success("No effluent samples currently exceed permit limits.")
    else:
        for _, r in over.iterrows():
            st.markdown(
                f"<div style='background:#7f1d1d;color:#fff;padding:10px 14px;"
                f"border-radius:6px;margin-bottom:6px;'>"
                f"<b>OVER LIMIT</b> &nbsp; {r['sample_date']} &nbsp;|&nbsp; "
                f"{r['parameter']} = <b>{r['value']} {r['unit']}</b> "
                f"(limit {r['limit']} {r['unit']}) &nbsp;|&nbsp; {r['location']}"
                f"</div>",
                unsafe_allow_html=True,
            )

    left, right = st.columns(2)

    with left:
        st.subheader("📉 Low-stock alerts")
        if low.empty:
            st.success("All chemicals are above their reorder levels.")
        else:
            show = low[["name", "on_hand", "reorder_level", "unit"]].rename(
                columns={"name": "Chemical", "on_hand": "On hand",
                         "reorder_level": "Reorder level", "unit": "Unit"}
            )
            st.dataframe(show, use_container_width=True, hide_index=True)

    with right:
        st.subheader("⏳ Expiring chemicals (≤30 days)")
        if expiring.empty:
            st.success("No lots expiring within 30 days.")
        else:
            show = expiring.rename(columns={
                "lot_number": "Lot", "chemical": "Chemical", "expiry_date": "Expires"})
            st.dataframe(show, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("🕒 Recent activity")
    recent = query_df("""
        SELECT txn_date AS date, txn_type AS type, c.name AS chemical,
               t.quantity AS qty, c.unit AS unit, t.note AS note
        FROM transactions t
        JOIN lots l ON l.id = t.lot_id
        JOIN chemicals c ON c.id = l.chemical_id
        UNION ALL
        SELECT event_date, event_type, COALESCE(c.name, '(tank op)'),
               m.quantity, COALESCE(c.unit, ''),
               tk.name || ' - ' || m.note
        FROM maintenance m
        JOIN tanks tk ON tk.id = m.tank_id
        LEFT JOIN chemicals c ON c.id = m.chemical_id
        ORDER BY date DESC
        LIMIT 15
    """)
    st.dataframe(recent, use_container_width=True, hide_index=True)


def page_catalog():
    st.title("📚 Chemical Catalog")
    df = chemicals_df()
    inv = inventory_on_hand()[["chemical_id", "on_hand"]]
    merged = df.merge(inv, left_on="id", right_on="chemical_id", how="left")
    merged["on_hand"] = merged["on_hand"].fillna(0)
    show = merged[["name", "cas_number", "hazard_class", "unit",
                   "reorder_level", "on_hand", "sds_link"]].rename(columns={
        "name": "Chemical", "cas_number": "CAS #", "hazard_class": "Hazard class",
        "unit": "Unit", "reorder_level": "Reorder level", "on_hand": "On hand",
        "sds_link": "SDS",
    })
    st.dataframe(
        show, use_container_width=True, hide_index=True,
        column_config={"SDS": st.column_config.LinkColumn("SDS", display_text="SDS")},
    )

    with st.expander("➕ Add a new chemical to the catalog"):
        with st.form("add_chem"):
            name = st.text_input("Name")
            cas = st.text_input("CAS number")
            hazard = st.text_input("Hazard class")
            sds = st.text_input("SDS link", value="https://sds.example.com/")
            unit = st.selectbox("Unit", ["kg", "L", "g", "mL"])
            reorder = st.number_input("Reorder level", min_value=0.0, value=25.0)
            if st.form_submit_button("Add chemical"):
                if not name:
                    st.error("Name is required.")
                else:
                    run_write(
                        "INSERT INTO chemicals (name, cas_number, hazard_class, sds_link, unit, reorder_level)"
                        " VALUES (?,?,?,?,?,?)",
                        (name, cas, hazard, sds, unit, reorder),
                    )
                    st.cache_data.clear()
                    st.success(f"Added {name}.")
                    st.rerun()


def page_log_delivery():
    st.title("📦 Log Delivery (Stock Up)")
    st.caption("Record a chemical delivery as a new lot with an intake transaction.")
    chems = chemicals_df()
    name_to_id = {r["name"]: r["id"] for _, r in chems.iterrows()}
    unit_by_id = {r["id"]: r["unit"] for _, r in chems.iterrows()}

    with st.form("delivery"):
        cname = st.selectbox("Chemical", list(name_to_id.keys()))
        unit = unit_by_id[name_to_id[cname]]
        col1, col2 = st.columns(2)
        with col1:
            lot_number = st.text_input("Lot number")
            quantity = st.number_input(f"Quantity received ({unit})", min_value=0.0, value=100.0)
            supplier = st.text_input("Supplier")
        with col2:
            received = st.date_input("Received date", value=today())
            expiry = st.date_input("Expiry date", value=today() + timedelta(days=365))
        note = st.text_input("Note", value="Delivery received")
        if st.form_submit_button("Log delivery"):
            cid = name_to_id[cname]
            lot_id = run_write(
                "INSERT INTO lots (chemical_id, lot_number, received_date, expiry_date, quantity, supplier)"
                " VALUES (?,?,?,?,?,?)",
                (cid, lot_number, received.isoformat(), expiry.isoformat(), quantity, supplier),
            )
            run_write(
                "INSERT INTO transactions (lot_id, txn_date, txn_type, quantity, note)"
                " VALUES (?,?,?,?,?)",
                (lot_id, received.isoformat(), "intake", quantity, note or supplier),
            )
            st.cache_data.clear()
            st.success(f"Logged delivery of {quantity} {unit} of {cname} (lot {lot_number}).")


def page_tanks():
    st.title("🛁 Process Tanks")
    tanks = query_df("SELECT * FROM tanks ORDER BY name")
    for _, t in tanks.iterrows():
        st.subheader(f"{t['name']}")
        st.caption(f"{t['process_type']} · {t['volume_l']:.0f} L · {t['location']}")
        latest = query_df("""
            SELECT reading_date, ph, temperature_c, concentration, note
            FROM readings WHERE tank_id = ? ORDER BY reading_date DESC LIMIT 1
        """, (int(t["id"]),))
        if latest.empty:
            st.info("No readings yet.")
        else:
            r = latest.iloc[0]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Latest reading", r["reading_date"])
            m2.metric("pH", f"{r['ph']:.2f}" if pd.notna(r["ph"]) else "—")
            m3.metric("Temp (°C)", f"{r['temperature_c']:.1f}" if pd.notna(r["temperature_c"]) else "—")
            m4.metric("Conc (g/L)", f"{r['concentration']:.1f}" if pd.notna(r["concentration"]) else "—")
        with st.expander("Reading history & maintenance"):
            hist = query_df("""
                SELECT reading_date AS Date, ph AS pH, temperature_c AS "Temp °C",
                       concentration AS "Conc g/L", note AS Note
                FROM readings WHERE tank_id = ? ORDER BY reading_date DESC
            """, (int(t["id"]),))
            st.dataframe(hist, use_container_width=True, hide_index=True)
            maint = query_df("""
                SELECT m.event_date AS Date, m.event_type AS Event,
                       COALESCE(c.name,'—') AS Chemical, m.quantity AS Qty, m.note AS Note
                FROM maintenance m LEFT JOIN chemicals c ON c.id = m.chemical_id
                WHERE m.tank_id = ? ORDER BY m.event_date DESC
            """, (int(t["id"]),))
            st.markdown("**Maintenance events**")
            st.dataframe(maint, use_container_width=True, hide_index=True)
        st.divider()


def page_log_bath():
    st.title("🔧 Log Bath Addition / Cleaning (Stock Down)")
    st.caption("Record a maintenance event. Additions consume chemical stock.")
    tanks = query_df("SELECT id, name FROM tanks ORDER BY name")
    tank_map = {r["name"]: r["id"] for _, r in tanks.iterrows()}
    chems = chemicals_df()
    name_to_id = {r["name"]: r["id"] for _, r in chems.iterrows()}
    unit_by_id = {r["id"]: r["unit"] for _, r in chems.iterrows()}

    with st.form("bath"):
        tname = st.selectbox("Tank", list(tank_map.keys()))
        etype = st.selectbox("Event type", ["addition", "cleaning", "dump"])
        event_date = st.date_input("Event date", value=today())
        cname = st.selectbox("Chemical (for additions)", ["(none)"] + list(name_to_id.keys()))
        quantity = st.number_input("Quantity added", min_value=0.0, value=10.0)
        note = st.text_input("Note", value="")
        submitted = st.form_submit_button("Log event")

    if submitted:
        tid = int(tank_map[tname])
        cid = name_to_id[cname] if cname != "(none)" else None
        qty = quantity if etype == "addition" and cid else None
        run_write(
            "INSERT INTO maintenance (tank_id, event_date, event_type, chemical_id, quantity, note)"
            " VALUES (?,?,?,?,?,?)",
            (tid, event_date.isoformat(), etype, cid, qty, note),
        )
        # An addition draws stock down via a usage transaction against the
        # most recent lot of that chemical that still has quantity on hand.
        if etype == "addition" and cid and quantity > 0:
            lot = query_df("""
                SELECT id FROM lots WHERE chemical_id = ?
                ORDER BY received_date DESC LIMIT 1
            """, (cid,))
            if not lot.empty:
                lot_id = int(lot.iloc[0]["id"])
                run_write(
                    "INSERT INTO transactions (lot_id, txn_date, txn_type, quantity, note)"
                    " VALUES (?,?,?,?,?)",
                    (lot_id, event_date.isoformat(), "usage", quantity,
                     f"Bath addition to {tname}"),
                )
                st.cache_data.clear()
                unit = unit_by_id[cid]
                st.success(f"Logged {etype}: {quantity} {unit} of {cname} into {tname}. Stock reduced.")
            else:
                st.cache_data.clear()
                st.warning(f"Logged {etype}, but no lot found for {cname} to draw stock from.")
        else:
            st.cache_data.clear()
            st.success(f"Logged {etype} on {tname}.")


def page_effluent():
    st.title("💧 Add Effluent Sample")
    st.caption("Record a wastewater measurement; it is flagged green (pass) or red (over limit).")
    limits = permit_limits_map()

    with st.form("effluent"):
        sample_date = st.date_input("Sample date", value=today())
        location = st.text_input("Sample location / outfall", value="Outfall 001")
        parameter = st.selectbox("Parameter", list(limits.keys()))
        value = st.number_input("Measured value", min_value=0.0, value=0.0, step=0.01, format="%.3f")
        note = st.text_input("Note", value="Composite sample")
        submitted = st.form_submit_button("Add sample")

    if submitted:
        max_value, unit = limits[parameter]
        run_write(
            "INSERT INTO effluent_samples (sample_date, location, parameter, value, unit, note)"
            " VALUES (?,?,?,?,?,?)",
            (sample_date.isoformat(), location, parameter, value, unit, note),
        )
        st.cache_data.clear()
        if value > max_value:
            st.markdown(
                f"<div style='background:#7f1d1d;color:#fff;padding:12px 16px;border-radius:6px;'>"
                f"🚨 <b>OVER LIMIT</b>: {parameter} = {value} {unit} exceeds permit limit "
                f"{max_value} {unit}.</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<div style='background:#14532d;color:#fff;padding:12px 16px;border-radius:6px;'>"
                f"✅ <b>WITHIN LIMIT</b>: {parameter} = {value} {unit} (limit {max_value} {unit}).</div>",
                unsafe_allow_html=True,
            )

    st.divider()
    st.subheader("Recent effluent samples")
    eff = query_df("SELECT * FROM effluent_samples ORDER BY sample_date DESC, id DESC")
    if not eff.empty:
        eff["limit"] = eff["parameter"].map(lambda p: limits.get(p, (None, None))[0])
        eff["status"] = eff.apply(
            lambda r: "OVER LIMIT" if (r["limit"] is not None and r["value"] > r["limit"])
            else "OK", axis=1)
        show = eff[["sample_date", "location", "parameter", "value", "unit", "limit", "status"]].rename(
            columns={"sample_date": "Date", "location": "Location", "parameter": "Parameter",
                     "value": "Value", "unit": "Unit", "limit": "Limit", "status": "Status"})

        def highlight(row):
            color = "background-color:#7f1d1d;color:#fff" if row["Status"] == "OVER LIMIT" \
                else "background-color:#14532d;color:#fff"
            return [color] * len(row)

        st.dataframe(show.style.apply(highlight, axis=1), use_container_width=True, hide_index=True)


def page_report():
    st.title("📊 Current Inventory Report")
    inv = inventory_on_hand()
    inv["status"] = inv.apply(
        lambda r: "LOW" if r["on_hand"] <= r["reorder_level"] else "OK", axis=1)
    show = inv[["name", "cas_number", "unit", "reorder_level", "on_hand", "status"]].rename(
        columns={"name": "Chemical", "cas_number": "CAS #", "unit": "Unit",
                 "reorder_level": "Reorder level", "on_hand": "On hand", "status": "Status"})
    st.dataframe(show, use_container_width=True, hide_index=True)

    csv = show.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Export current inventory to CSV",
        data=csv,
        file_name=f"inventory_report_{today().isoformat()}.csv",
        mime="text/csv",
    )


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
def main():
    ensure_db()
    st.sidebar.title("🧪 Chem Inventory")
    st.sidebar.caption("Metal Finishing Compliance Tracker")
    pages = {
        "Dashboard": page_dashboard,
        "Chemical Catalog": page_catalog,
        "Log Delivery (Stock Up)": page_log_delivery,
        "Tanks": page_tanks,
        "Log Bath Addition/Cleaning": page_log_bath,
        "Add Effluent Sample": page_effluent,
        "Inventory Report": page_report,
    }
    choice = st.sidebar.radio("Navigate", list(pages.keys()))
    st.sidebar.divider()
    if st.sidebar.button("🔄 Reset demo data"):
        seed.main()
        st.cache_data.clear()
        st.sidebar.success("Demo data reset.")
    pages[choice]()


if __name__ == "__main__":
    main()
