# chem-inventory-demo

**Metal Finishing Chemical Inventory & Compliance Tracker** — a working demo web
app built with Python, [Streamlit](https://streamlit.io), and SQLite.

It tracks a chemical catalog, inventory lots with intake/usage transactions,
process tanks/baths with chemistry readings, bath maintenance events, and
wastewater (effluent) samples compared against permit limits.

## Features / Pages

- **Dashboard** — low-stock alerts, expiring chemicals, recent activity, and any
  over-limit effluent samples highlighted in red.
- **Chemical Catalog** — name, CAS number, hazard class, SDS link, unit, reorder
  level, and current on-hand quantity. Add new chemicals inline.
- **Log Delivery (Stock Up)** — record a delivery as a new lot + intake transaction.
- **Tanks** — each process tank with its latest pH / temperature / concentration
  reading plus full reading and maintenance history.
- **Log Bath Addition/Cleaning (Stock Down)** — record additions, cleanings, and
  dumps; additions draw down chemical stock automatically.
- **Add Effluent Sample** — enter a wastewater measurement, flagged **green**
  (within limit) or **red** (over permit limit).
- **Inventory Report** — current on-hand inventory with a one-click **CSV export**.
- **Assistant** — an AI chat assistant powered by **Google Gemini** (function
  calling). Ask open-ended questions, log deliveries, record bath actions, add
  effluent samples, edit reorder/permit levels, and render charts inline — the
  model picks the right tool. Reads are guarded to read-only SQL; all writes go
  through specific parameterized functions. See **AI Assistant** below.

## Setup & Run

```bash
# 1. (optional) create a virtual environment
python3 -m venv .venv && source .venv/bin/activate

# 2. install dependencies
pip install -r requirements.txt

# 3. (optional) seed/refresh the demo database
python3 seed.py

# 4. run the app
streamlit run app.py
```

Then open the URL Streamlit prints (default <http://localhost:8501>).

> The SQLite database (`inventory.db`) is created and seeded automatically on
> first launch if it does not already exist. Use the **Reset demo data** button
> in the sidebar to reload fresh seed data at any time.

## AI Assistant (Google Gemini)

The **Assistant** page is a chat interface backed by Google's Gemini
(`gemini-2.5-flash`) using the official [`google-genai`](https://pypi.org/project/google-genai/)
SDK and **function calling**. The model is given a toolbox and decides what to
do from plain English:

- **Read & analyze** — `run_query` (read-only SELECT, hard-guarded against any
  write) and `get_dashboard_summary`.
- **Add** — `log_delivery`, `log_bath_action`, `add_effluent_sample`, `add_chemical`.
- **Edit** — `update_reorder_level`, `update_permit_limit`, `correct_reading`.
- **Chart** — `show_chart` renders line/bar/area charts inline in the chat.

Every tool call shows a transparent caption of what ran; writes pop a toast (and
balloons after a delivery). Chemical/tank/parameter names are fuzzy-matched to
existing records, so you never deal with IDs.

### Configure the API key

The key is read from `st.secrets["GEMINI_API_KEY"]`, falling back to the
`GEMINI_API_KEY` environment variable. It is **never** hardcoded, and
`.streamlit/secrets.toml` is gitignored.

```bash
# option A: Streamlit secrets (recommended)
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# then edit the file and paste your key

# option B: environment variable
export GEMINI_API_KEY="your-key-here"
```

Get a free key at <https://aistudio.google.com/apikey>. Without a key the rest of
the app still works; the Assistant page just shows setup instructions.

### Things to try

- *What's over limit right now?*
- *How much hydrochloric acid do we have on hand?*
- *Log a delivery of 50 kg nickel sulfate from MetalChem, lot NS-2026-099.*
- *Add an effluent sample: chromium 0.31 mg/L at Outfall 001 today.*
- *Bump sodium cyanide's reorder level to 30.*
- *Chart total chemical usage per day for the last two weeks.*
- *Summarize what happened this week and flag anything I should worry about.*

## Demo data

The seed loads ~12 metal-finishing chemicals (nickel sulfate, chromic acid,
sodium hydroxide, sulfuric/hydrochloric/nitric acid, copper sulfate, etc.),
3 process tanks (Watts nickel, hard chrome, alkaline soak), and roughly two
weeks of intake, usage, maintenance, and chemistry-reading activity. It
intentionally includes:

- **Sodium Cyanide** below its reorder level → low-stock alert.
- A **Nitric Acid** lot expiring within 30 days → expiring alert.
- A **Hexavalent Chromium** effluent sample of 0.41 mg/L against a 0.25 mg/L
  permit limit → red over-limit flag on the dashboard.

## Project layout

| File               | Purpose                                            |
| ------------------ | -------------------------------------------------- |
| `app.py`           | Streamlit app with all pages and the router.       |
| `assistant.py`     | Gemini-powered AI Assistant page and its tools.    |
| `db.py`            | SQLite connection helper and schema definition.    |
| `seed.py`          | Drops, recreates, and seeds the demo database.     |
| `requirements.txt` | Python dependencies.                               |
