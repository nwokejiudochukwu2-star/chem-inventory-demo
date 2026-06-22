"""AI Assistant page powered by Google Gemini (google-genai SDK).

The assistant uses Gemini function calling: a set of Python tool functions are
handed to the model, which decides what to call based on the user's message.
Read/analyze tools are read-only; writes go only through specific, parameterized
functions. Tool side-effects (captions, charts, toasts) are recorded as
"artifacts" so the conversation re-renders correctly across Streamlit reruns.
"""
import os
import re
import difflib
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import db

# Importing the SDK is guarded so the rest of the app still boots if the
# package is missing or its native deps are unavailable.
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
    GENAI_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - environment dependent
    GENAI_AVAILABLE = False
    GENAI_IMPORT_ERROR = str(exc)

MODEL = "gemini-2.5-flash"
TODAY = date(2026, 6, 22)  # fixed "today" so the seeded demo stays consistent
EXPIRY_WINDOW_DAYS = 30

SYSTEM_INSTRUCTION = """\
You are the AI assistant for a metal-finishing shop's chemical inventory and
wastewater-compliance system. You can read and analyze records, add new records,
edit existing ones, and render charts, all through the provided tools.

Guidelines:
- Decide which tool(s) to call based on what the user asks. Prefer the specific
  add/edit tools for any change; use run_query only for reading/analysis.
- Resolve chemical, tank, and parameter names yourself by their natural names;
  the user never deals with numeric IDs. If a name is fuzzy, the tools match the
  closest record and tell you what they matched.
- For open-ended questions, write a single read-only SQL SELECT against the
  schema and call run_query. To visualize, call show_chart.
- Be concise. After doing something, confirm in one or two sentences exactly
  what you did (names, quantities, and whether a sample passed or exceeded its
  permit limit).

Database schema (SQLite):
  chemicals(id, name, cas_number, category, hazard_class, sds_link, unit, reorder_level)
  lots(id, chemical_id, lot_number, received_date, expiry_date, quantity, supplier)
  transactions(id, lot_id, txn_date, txn_type['intake'|'usage'], quantity, note)
  tanks(id, name, process_type, volume_l, location)
  readings(id, tank_id, reading_date, ph, temperature_c, concentration, note)
  maintenance(id, tank_id, event_date, event_type['addition'|'cleaning'|'dump'], chemical_id, quantity, note)
  permit_limits(id, parameter, max_value, unit)
  effluent_samples(id, sample_date, location, parameter, value, unit, note)

On-hand stock for a chemical = SUM(intake) - SUM(usage) across its lots'
transactions. Today's date is 2026-06-22.
"""


# --------------------------------------------------------------------------- #
# Artifact plumbing (transparency: captions, charts, toasts in the chat)
# --------------------------------------------------------------------------- #
def _artifacts():
    return st.session_state.setdefault("_assistant_artifacts", [])


def _caption(text):
    _artifacts().append({"type": "caption", "text": text})


def _toast(msg):
    _artifacts().append({"type": "toast", "msg": msg})


def _balloons():
    _artifacts().append({"type": "balloons"})


def _table(rows):
    _artifacts().append({"type": "dataframe", "rows": rows})


def _chart(sql, chart_type, x, y, title):
    _artifacts().append({"type": "chart", "sql": sql, "chart_type": chart_type,
                         "x": x, "y": y, "title": title})


def render_artifact(art):
    """Re-render a stored visual artifact (skips transient toast/balloons)."""
    t = art["type"]
    if t == "caption":
        st.caption(f"🛠️ {art['text']}")
    elif t == "dataframe":
        rows = art.get("rows", [])
        if rows:
            with st.expander(f"Query result · {len(rows)} row(s)", expanded=len(rows) <= 8):
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("Query returned no rows.")
    elif t == "chart":
        _draw_chart(art)


def _draw_chart(art):
    try:
        df = _read_sql(art["sql"])
        if art.get("title"):
            st.markdown(f"**{art['title']}**")
        if df.empty:
            st.info("No data to chart.")
            return
        x, y = art.get("x"), art.get("y")
        if x and x in df.columns:
            df = df.set_index(x)
        if y and y in df.columns:
            data = df[[y]]
        else:
            data = df.select_dtypes("number")
        ctype = (art.get("chart_type") or "line").lower()
        if ctype == "bar":
            st.bar_chart(data)
        elif ctype == "area":
            st.area_chart(data)
        else:
            st.line_chart(data)
    except Exception as exc:
        st.warning(f"Could not render chart: {exc}")


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
def _conn():
    return db.get_connection()


def _read_sql(sql, params=()):
    conn = _conn()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def _today_iso():
    return TODAY.isoformat()


def _resolve(table, name):
    """Fuzzy-match `name` to a row in `table` (chemicals/tanks). Returns the
    sqlite Row of the best match, or None."""
    conn = _conn()
    try:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    names = [r["name"] for r in rows]
    low = name.strip().lower()
    # Exact (case-insensitive) first.
    for r in rows:
        if r["name"].lower() == low:
            return r
    # Substring match.
    subs = [r for r in rows if low in r["name"].lower() or r["name"].lower() in low]
    if len(subs) == 1:
        return subs[0]
    # Fuzzy closest.
    match = difflib.get_close_matches(name, names, n=1, cutoff=0.4)
    if match:
        for r in rows:
            if r["name"] == match[0]:
                return r
    return subs[0] if subs else None


def _resolve_parameter(name):
    """Fuzzy-match an effluent/permit parameter name to a permit_limits row."""
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM permit_limits").fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    params = [r["parameter"] for r in rows]
    low = name.strip().lower()
    for r in rows:
        if r["parameter"].lower() == low:
            return r
    subs = [r for r in rows if low in r["parameter"].lower() or r["parameter"].lower() in low]
    if subs:
        return subs[0]
    match = difflib.get_close_matches(name, params, n=1, cutoff=0.4)
    if match:
        for r in rows:
            if r["parameter"] == match[0]:
                return r
    return None


def _on_hand(chemical_id):
    conn = _conn()
    try:
        row = conn.execute("""
            SELECT COALESCE(SUM(CASE WHEN t.txn_type='intake' THEN t.quantity
                                     WHEN t.txn_type='usage'  THEN -t.quantity
                                     ELSE 0 END),0) AS oh
            FROM lots l LEFT JOIN transactions t ON t.lot_id=l.id
            WHERE l.chemical_id=?
        """, (chemical_id,)).fetchone()
        return round(row["oh"], 3) if row else 0
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Read-only SQL guard
# --------------------------------------------------------------------------- #
_FORBIDDEN = ["insert", "update", "delete", "drop", "alter", "create",
              "replace", "attach", "detach", "pragma", "vacuum", "reindex"]


def _guard_select(sql):
    """Raise ValueError unless `sql` is a single read-only SELECT/CTE."""
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise ValueError("Only a single statement is allowed (no semicolons).")
    low = s.lower()
    if not (low.startswith("select") or low.startswith("with")):
        raise ValueError("Only SELECT queries are allowed.")
    for w in _FORBIDDEN:
        if re.search(r"\b" + w + r"\b", low):
            raise ValueError(f"Keyword '{w}' is not allowed in a read-only query.")
    return s


# --------------------------------------------------------------------------- #
# TOOLS — read & analyze
# --------------------------------------------------------------------------- #
def run_query(sql: str) -> dict:
    """Execute a single READ-ONLY SQL SELECT statement and return the rows.

    Use this to answer open-ended questions about inventory, usage, readings,
    maintenance, and effluent data. Only SELECT is permitted; any attempt to
    modify data is rejected.

    Args:
        sql: A single SQL SELECT (or WITH ... SELECT) statement.
    """
    try:
        safe = _guard_select(sql)
    except ValueError as e:
        _caption(f"run_query (rejected: {e})")
        return {"error": str(e)}
    df = _read_sql(safe)
    rows = df.to_dict("records")
    _caption(f"run_query({_short(sql)})")
    _table(rows[:200])
    return {"row_count": len(rows), "rows": rows[:200]}


def get_dashboard_summary() -> dict:
    """Return what needs attention: low-stock chemicals, chemicals expiring
    within 30 days, effluent samples currently over their permit limit, and
    recent activity."""
    _caption("get_dashboard_summary()")
    conn = _conn()
    try:
        inv = pd.read_sql_query("""
            SELECT c.name, c.unit, c.reorder_level,
                   COALESCE(SUM(CASE WHEN t.txn_type='intake' THEN t.quantity
                                     WHEN t.txn_type='usage'  THEN -t.quantity
                                     ELSE 0 END),0) AS on_hand
            FROM chemicals c
            LEFT JOIN lots l ON l.chemical_id=c.id
            LEFT JOIN transactions t ON t.lot_id=l.id
            GROUP BY c.id
        """, conn)
        horizon = (TODAY + timedelta(days=EXPIRY_WINDOW_DAYS)).isoformat()
        expiring = pd.read_sql_query("""
            SELECT l.lot_number, c.name AS chemical, l.expiry_date
            FROM lots l JOIN chemicals c ON c.id=l.chemical_id
            WHERE l.expiry_date IS NOT NULL AND l.expiry_date <= ?
            ORDER BY l.expiry_date
        """, conn, params=(horizon,))
        eff = pd.read_sql_query("""
            SELECT e.sample_date, e.location, e.parameter, e.value, e.unit,
                   p.max_value AS limit_value
            FROM effluent_samples e
            LEFT JOIN permit_limits p ON p.parameter=e.parameter
            ORDER BY e.sample_date DESC
        """, conn)
        recent = pd.read_sql_query("""
            SELECT t.txn_date AS date, t.txn_type AS type, c.name AS item, t.quantity AS qty
            FROM transactions t
            JOIN lots l ON l.id=t.lot_id JOIN chemicals c ON c.id=l.chemical_id
            ORDER BY t.txn_date DESC LIMIT 8
        """, conn)
    finally:
        conn.close()
    low = inv[inv["on_hand"] <= inv["reorder_level"]]
    over = eff[eff["limit_value"].notna() & (eff["value"] > eff["limit_value"])]
    return {
        "low_stock": low[["name", "on_hand", "reorder_level", "unit"]].to_dict("records"),
        "expiring": expiring.to_dict("records"),
        "over_limit_effluent": over[["sample_date", "parameter", "value", "limit_value", "location"]].to_dict("records"),
        "recent_activity": recent.to_dict("records"),
    }


# --------------------------------------------------------------------------- #
# TOOLS — add (writes)
# --------------------------------------------------------------------------- #
def log_delivery(chemical: str, quantity: float, supplier: str,
                 lot_number: str = None, expiry_date: str = None) -> dict:
    """Log a chemical delivery: create a new inventory lot and an intake
    transaction (stock up).

    Args:
        chemical: Name of the chemical (fuzzy-matched to the catalog).
        quantity: Quantity received, in the chemical's unit.
        supplier: Supplier name.
        lot_number: Optional lot/batch number.
        expiry_date: Optional expiry date as YYYY-MM-DD (defaults to +1 year).
    """
    row = _resolve("chemicals", chemical)
    if not row:
        return {"error": f"No chemical matching '{chemical}'."}
    received = _today_iso()
    if not expiry_date:
        expiry_date = (TODAY + timedelta(days=365)).isoformat()
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("""INSERT INTO lots (chemical_id, lot_number, received_date,
                       expiry_date, quantity, supplier) VALUES (?,?,?,?,?,?)""",
                    (row["id"], lot_number, received, expiry_date, quantity, supplier))
        lot_id = cur.lastrowid
        cur.execute("""INSERT INTO transactions (lot_id, txn_date, txn_type, quantity, note)
                       VALUES (?,?,?,?,?)""",
                    (lot_id, received, "intake", quantity, f"Delivery received - {supplier}"))
        conn.commit()
    finally:
        conn.close()
    oh = _on_hand(row["id"])
    _caption(f"log_delivery(chemical='{row['name']}', quantity={quantity}, supplier='{supplier}'"
             + (f", lot_number='{lot_number}'" if lot_number else "") + ")")
    _toast(f"Logged delivery: {quantity} {row['unit']} {row['name']}")
    _balloons()
    return {"ok": True, "chemical": row["name"], "lot_number": lot_number,
            "received_date": received, "expiry_date": expiry_date,
            "new_on_hand": oh, "unit": row["unit"]}


def log_bath_action(tank: str, action: str, chemical: str = None,
                    quantity: float = None, note: str = None) -> dict:
    """Record a bath maintenance event on a tank: an addition, cleaning, or dump.
    An 'addition' of a chemical draws that chemical's stock down.

    Args:
        tank: Tank name (fuzzy-matched).
        action: One of 'addition', 'cleaning', 'dump'.
        chemical: Chemical name (required for additions).
        quantity: Quantity added (for additions).
        note: Optional free-text note.
    """
    trow = _resolve("tanks", tank)
    if not trow:
        return {"error": f"No tank matching '{tank}'."}
    action = (action or "").strip().lower()
    if action not in ("addition", "cleaning", "dump"):
        return {"error": "action must be 'addition', 'cleaning', or 'dump'."}
    crow = _resolve("chemicals", chemical) if chemical else None
    if action == "addition" and not crow:
        return {"error": "An addition requires a known chemical."}
    event_date = _today_iso()
    qty = quantity if (action == "addition" and crow) else None
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("""INSERT INTO maintenance (tank_id, event_date, event_type,
                       chemical_id, quantity, note) VALUES (?,?,?,?,?,?)""",
                    (trow["id"], event_date, action, crow["id"] if crow else None, qty, note))
        drew_stock = False
        if action == "addition" and crow and quantity and quantity > 0:
            lot = cur.execute("""SELECT id FROM lots WHERE chemical_id=?
                                 ORDER BY received_date DESC LIMIT 1""",
                              (crow["id"],)).fetchone()
            if lot:
                cur.execute("""INSERT INTO transactions (lot_id, txn_date, txn_type, quantity, note)
                               VALUES (?,?,?,?,?)""",
                            (lot["id"], event_date, "usage", quantity,
                             f"Bath addition to {trow['name']}"))
                drew_stock = True
        conn.commit()
    finally:
        conn.close()
    args = f"tank='{trow['name']}', action='{action}'"
    if crow:
        args += f", chemical='{crow['name']}'"
    if quantity:
        args += f", quantity={quantity}"
    _caption(f"log_bath_action({args})")
    _toast(f"Logged {action} on {trow['name']}")
    result = {"ok": True, "tank": trow["name"], "action": action}
    if crow:
        result["chemical"] = crow["name"]
        result["stock_reduced"] = drew_stock
        result["new_on_hand"] = _on_hand(crow["id"])
    return result


def add_effluent_sample(parameter: str, measured_value: float,
                        sample_point: str = None, sample_date: str = None) -> dict:
    """Log a wastewater (effluent) reading and compare it to the permit limit.

    Args:
        parameter: Pollutant parameter (e.g. 'Nickel', 'Hexavalent Chromium').
        measured_value: Measured concentration.
        sample_point: Optional sample location / outfall (default 'Outfall 001').
        sample_date: Optional date YYYY-MM-DD (default today).
    """
    prow = _resolve_parameter(parameter)
    param_name = prow["parameter"] if prow else parameter
    unit = prow["unit"] if prow else "mg/L"
    limit_value = prow["max_value"] if prow else None
    location = sample_point or "Outfall 001"
    sdate = sample_date or _today_iso()
    conn = _conn()
    try:
        conn.execute("""INSERT INTO effluent_samples (sample_date, location, parameter,
                        value, unit, note) VALUES (?,?,?,?,?,?)""",
                     (sdate, location, param_name, measured_value, unit, "Logged via assistant"))
        conn.commit()
    finally:
        conn.close()
    over = (limit_value is not None) and (measured_value > limit_value)
    _caption(f"add_effluent_sample(parameter='{param_name}', measured_value={measured_value}, "
             f"sample_point='{location}')")
    if over:
        _toast(f"⚠️ {param_name} OVER limit ({measured_value} > {limit_value} {unit})")
    else:
        _toast(f"{param_name} within limit")
    return {"ok": True, "parameter": param_name, "value": measured_value, "unit": unit,
            "limit": limit_value, "over_limit": over, "location": location, "date": sdate}


def add_chemical(name: str, unit: str, cas_number: str = None, category: str = None,
                 hazard_class: str = None, reorder_level: float = 0) -> dict:
    """Add a new chemical to the catalog.

    Args:
        name: Chemical name.
        unit: Stock unit (e.g. 'kg', 'L').
        cas_number: Optional CAS registry number.
        category: Optional category (e.g. 'Acid', 'Plating Salt').
        hazard_class: Optional hazard classification.
        reorder_level: Optional reorder threshold (default 0).
    """
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("""INSERT INTO chemicals (name, cas_number, category, hazard_class,
                       sds_link, unit, reorder_level) VALUES (?,?,?,?,?,?,?)""",
                    (name, cas_number, category, hazard_class,
                     "https://sds.example.com/", unit, reorder_level or 0))
        conn.commit()
        new_id = cur.lastrowid
    finally:
        conn.close()
    _caption(f"add_chemical(name='{name}', unit='{unit}'"
             + (f", category='{category}'" if category else "") + ")")
    _toast(f"Added chemical: {name}")
    return {"ok": True, "id": new_id, "name": name, "unit": unit, "category": category}


# --------------------------------------------------------------------------- #
# TOOLS — edit (updates)
# --------------------------------------------------------------------------- #
def update_reorder_level(chemical: str, new_level: float) -> dict:
    """Update the reorder level (low-stock threshold) for a chemical.

    Args:
        chemical: Chemical name (fuzzy-matched).
        new_level: New reorder level.
    """
    row = _resolve("chemicals", chemical)
    if not row:
        return {"error": f"No chemical matching '{chemical}'."}
    conn = _conn()
    try:
        conn.execute("UPDATE chemicals SET reorder_level=? WHERE id=?", (new_level, row["id"]))
        conn.commit()
    finally:
        conn.close()
    _caption(f"update_reorder_level(chemical='{row['name']}', new_level={new_level})")
    _toast(f"{row['name']} reorder level → {new_level}")
    return {"ok": True, "chemical": row["name"], "old_level": row["reorder_level"],
            "new_level": new_level}


def update_permit_limit(parameter: str, new_limit: float) -> dict:
    """Update the permit limit (max allowed value) for a wastewater parameter.

    Args:
        parameter: Parameter name (fuzzy-matched).
        new_limit: New maximum permitted value.
    """
    prow = _resolve_parameter(parameter)
    if not prow:
        return {"error": f"No permit parameter matching '{parameter}'."}
    conn = _conn()
    try:
        conn.execute("UPDATE permit_limits SET max_value=? WHERE id=?", (new_limit, prow["id"]))
        conn.commit()
    finally:
        conn.close()
    _caption(f"update_permit_limit(parameter='{prow['parameter']}', new_limit={new_limit})")
    _toast(f"{prow['parameter']} limit → {new_limit} {prow['unit']}")
    return {"ok": True, "parameter": prow["parameter"], "old_limit": prow["max_value"],
            "new_limit": new_limit, "unit": prow["unit"]}


def correct_reading(reading_id: int, field: str, new_value: str) -> dict:
    """Correct a single field of a tank chemistry reading. Use run_query first to
    find the reading's id if needed.

    Args:
        reading_id: The id of the row in the readings table.
        field: One of 'ph', 'temperature_c', 'concentration', 'note'.
        new_value: The corrected value (numeric for ph/temperature_c/concentration).
    """
    field = (field or "").strip().lower()
    allowed = {"ph", "temperature_c", "concentration", "note"}
    if field not in allowed:
        return {"error": f"field must be one of {sorted(allowed)}."}
    value = new_value
    if field != "note":
        try:
            value = float(new_value)
        except (TypeError, ValueError):
            return {"error": f"{field} must be numeric."}
    conn = _conn()
    try:
        cur = conn.cursor()
        existing = cur.execute("SELECT * FROM readings WHERE id=?", (reading_id,)).fetchone()
        if not existing:
            return {"error": f"No reading with id {reading_id}."}
        cur.execute(f"UPDATE readings SET {field}=? WHERE id=?", (value, reading_id))
        conn.commit()
    finally:
        conn.close()
    _caption(f"correct_reading(reading_id={reading_id}, field='{field}', new_value={new_value})")
    _toast(f"Reading #{reading_id}: {field} → {new_value}")
    return {"ok": True, "reading_id": reading_id, "field": field,
            "old_value": existing[field], "new_value": value}


# --------------------------------------------------------------------------- #
# TOOLS — chart
# --------------------------------------------------------------------------- #
def show_chart(sql: str, chart_type: str, x: str, y: str, title: str = "") -> dict:
    """Run a READ-ONLY SELECT and render the result inline as a chart.

    Args:
        sql: A single SELECT producing the columns to plot.
        chart_type: 'line', 'bar', or 'area'.
        x: Column name for the x-axis (categories or dates).
        y: Column name for the y-axis (numeric value).
        title: Optional chart title.
    """
    try:
        safe = _guard_select(sql)
    except ValueError as e:
        _caption(f"show_chart (rejected: {e})")
        return {"error": str(e)}
    # Validate the query actually runs and yields rows.
    df = _read_sql(safe)
    _caption(f"show_chart(chart_type='{chart_type}', x='{x}', y='{y}')")
    _chart(safe, chart_type, x, y, title)
    return {"ok": True, "rows": len(df), "columns": list(df.columns),
            "chart_type": chart_type}


TOOLS = [run_query, get_dashboard_summary, log_delivery, log_bath_action,
         add_effluent_sample, add_chemical, update_reorder_level,
         update_permit_limit, correct_reading, show_chart]


# --------------------------------------------------------------------------- #
# Gemini client / chat session
# --------------------------------------------------------------------------- #
def _short(sql, n=80):
    s = " ".join(sql.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _api_key():
    key = None
    try:
        key = st.secrets.get("GEMINI_API_KEY")
    except Exception:
        key = None
    return key or os.environ.get("GEMINI_API_KEY")


def _new_chat():
    client = genai.Client(api_key=_api_key())
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=TOOLS,
        temperature=0.2,
    )
    return client.chats.create(model=MODEL, config=config)


def _friendly_error(exc):
    msg = str(exc)
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower() \
            or "rate" in msg.lower():
        return ("⏳ The Gemini free-tier rate limit was hit. Please wait a few "
                "seconds and try again — your message wasn't lost.")
    return f"⚠️ Sorry, something went wrong talking to Gemini:\n\n`{msg}`"


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
def page_assistant():
    st.title("🤖 AI Assistant")
    st.caption("Powered by Google Gemini · ask questions, log deliveries, chart usage, and more.")

    if not GENAI_AVAILABLE:
        st.error("The `google-genai` package isn't available in this environment.")
        st.code(f"pip install google-genai\n\n# import error: {GENAI_IMPORT_ERROR}")
        return

    if not _api_key():
        st.warning("No Gemini API key found.")
        st.markdown(
            "Add your key to **`.streamlit/secrets.toml`**:\n\n"
            "```toml\nGEMINI_API_KEY = \"your-key-here\"\n```\n\n"
            "…or set the `GEMINI_API_KEY` environment variable, then reload. "
            "Get a free key at https://aistudio.google.com/apikey."
        )
        return

    history = st.session_state.setdefault("assistant_history", [])
    if "gemini_chat" not in st.session_state:
        try:
            st.session_state.gemini_chat = _new_chat()
        except Exception as exc:
            st.error(_friendly_error(exc))
            return

    with st.sidebar:
        if st.button("🧹 Clear conversation"):
            st.session_state.assistant_history = []
            st.session_state.pop("gemini_chat", None)
            st.rerun()

    if not history:
        st.info(
            "Try asking:\n"
            "- *What's over limit right now?*\n"
            "- *How much hydrochloric acid do we have on hand?*\n"
            "- *Log a delivery of 50 kg nickel sulfate from MetalChem, lot NS-2026-099.*\n"
            "- *Add an effluent sample: chromium 0.31 mg/L at Outfall 001 today.*\n"
            "- *Bump sodium cyanide's reorder level to 30.*\n"
            "- *Chart total chemical usage per day for the last two weeks.*\n"
            "- *Summarize what happened this week and flag anything I should worry about.*"
        )

    # Replay prior conversation.
    for msg in history:
        with st.chat_message(msg["role"]):
            for art in msg.get("artifacts", []):
                render_artifact(art)
            if msg.get("content"):
                st.markdown(msg["content"])

    prompt = st.chat_input("Message the assistant…")
    if not prompt:
        return

    history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        st.session_state["_assistant_artifacts"] = []
        try:
            with st.spinner("Thinking…"):
                response = st.session_state.gemini_chat.send_message(prompt)
            text = (getattr(response, "text", None) or "").strip() or "Done."
        except Exception as exc:
            text = _friendly_error(exc)
        arts = st.session_state.get("_assistant_artifacts", [])
        for art in arts:
            render_artifact(art)
        st.markdown(text)
        # Fire transient effects only on this fresh turn.
        for art in arts:
            if art["type"] == "toast":
                st.toast(art["msg"], icon="✅")
            elif art["type"] == "balloons":
                st.balloons()

    history.append({"role": "assistant", "content": text, "artifacts": arts})
