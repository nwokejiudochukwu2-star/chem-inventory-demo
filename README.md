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
| `db.py`            | SQLite connection helper and schema definition.    |
| `seed.py`          | Drops, recreates, and seeds the demo database.     |
| `requirements.txt` | Python dependencies.                               |
