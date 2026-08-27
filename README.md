# Prometheus - MissionChief Bot (US Server) - V4

**Prometheus V4** is an advanced automation bot designed for the US server of the browser game [MissionChief](https://www.missionchief.com).

Built with Python and Playwright, Prometheus handles mission dispatching, intelligent fleet management, prisoner/patient transport, and station personnel recruitment. V4 introduces a robust **CLI Menu System**, allowing you to choose specifically between mission dispatching, transport logic, or running both simultaneously across multiple browser threads.

> **Note:** This project is inspired by [MissionchiefBot-X](https://github.com/NatesHonor/MissionchiefBot-X).

## 🚀 Key Features (V4)

  * **Smart Dispatching:** Analyzes mission requirements (including Water, Foam, SWAT, K9, and Personnel counts) and selects the appropriate vehicles based on your actual fleet capabilities.
  * **Intelligent Transport:**
      * Automatically transports patients to the nearest hospital.
      * Transports prisoners to cells.
      * **Auto-Release:** Automatically releases prisoners if no cells are available.
  * **Personnel Manager:** Automatically checks stations and hires new personnel based on your configuration (1-3 days or Automatic).
  * **CLI Menu System:** Choose your operation mode on startup (Missions & Transport, Missions Only, or Transport Only).
  * **Fleet Indexing:** Scrapes and indexes your personal vehicle IDs to map generic names (e.g., "Type 1 Engine") to your specific system IDs.
  * **Alliance Integration:**
      * **Sharing:** Options to automatically share missions.
      * **Filtering:** Options to process or ignore alliance missions.
  * **Multi-Threading:** Configure multiple browser instances to handle high mission volumes efficiently.
  * **Headless Mode:** Run the bot in the background without visible windows.

**🌍 International Support:** Now supports **19 servers** (us, uk, de, fr, nl, au, cz, dk, fi, it, pl, pt, se, no, kr, es, jp, ro, ru) via dashboard. Select your region in the dashboard → **Check** → **Download/Sync** — smart `etag/md5` diff, only the concerned region, no re-download if unchanged. The bot automatically uses `assets_cache/{code}` or falls back to bundled `us/`.

## 📋 Prerequisites

  * Python 3.8+
  * [Playwright](https://playwright.dev/python/)
  * Google Chrome / Chromium installed

## 🛠️ Installation

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/Theowlasticot/prometheus.git
    cd prometheus
    ```

2.  **Create & activate venv (required — do not use system `python`):**

    ```bash
    python3 -m venv venv
    source venv/bin/activate  # Windows: venv\Scripts\activate
    which python  # must point to .../Prometheus/venv/bin/python
    ```

3.  **Install dependencies (inside venv):**

    ```bash
    pip install -r requirements.txt
    ```

4.  **Install Playwright browsers:**

    ```bash
    playwright install  # or playwright install chromium
    ```

> **If `uvicorn` not found or `ModuleNotFoundError: fastapi`:** you used system `python` (`/usr/bin/python3 3.9`) not `venv`. Fix with `source venv/bin/activate` or `venv/bin/python -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8000 --reload`. **Do not run `python dashboard/app.py`** — it has no server; must be `uvicorn dashboard.app:app`.

### 🖥️ Dashboard — Everything from the UI (recommended)

```bash
# Terminal 1 — Dashboard (keep running)
venv/bin/python -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8000 --reload
# open http://127.0.0.1:8000  (or http://127.0.0.1:8000/api/docs for API)
# If port busy: lsof -ti:8000 | xargs kill -9  then retry
# If blank page: CDN tailwind/chart.js needs internet; server still runs
```

* **Stats:** missions/credits/fleet/personnel/alliance, activity sparkline, assets cache
* **Missions:** table `mission_data.json` with search
* **Config & Settings → Save Config:** credentials, browser (`headless`/`browsers 1-8`), delays, personnel (`hiring_mode`), alliance, **Server/Region 19 codes** + `auto_update` + `refresh_interval`, **Transport** (`allow_alliance_hospitals/cells`, `max_distance`), **Dispatch** (`min_percent`, `use_aar`), **Mission Filter** (`ignore_storm/event`, `min_credits`) — all `PUT /api/config` (redacted `***` for password) + **Server → Check (git `304`) → Download/Sync** smart `md5` only concerned region
* **Bot control (new):** `Start Bot [1/2/3]` / `Stop` / `Status` / `Logs` — controls `Main.py` without CLI

## ⚙️ Configuration

1.  Open `config.ini` in the root directory.
2.  Fill in your MissionChief credentials and adjust the settings. Below is the configuration structure for **V4**:

<!-- end list -->

```ini
[credentials]
username = your_email@example.com
password = your_password

[browser_settings]
headless = true
browsers = 2

[personnel_settings]
hiring_mode = 3  # 0 Disabled, 1/2/3 days, -1 Automatic (Premium)

[delays]
missions = 10
transport = 60
personnel_check = 3600

[mission_settings]
share_alliance = true
process_alliance = true

[server_settings]
code = us  # 19 codes: us/uk/de/fr/nl/au/cz/dk/fi/it/pl/pt/se/no/kr/es/jp/ro/ru
auto_update = true
refresh_interval = 3600
cache_dir = assets_cache
manifest_url = https://raw.githubusercontent.com/cfHxqA/Mission-Chief.Bot/master/Assets.json
server_manifest_url = https://raw.githubusercontent.com/cfHxqA/Mission-Chief.Bot/master/Assets/Server.json

[transport_settings]
allow_alliance_hospitals = true
allow_alliance_cells = true
max_distance = 0  # km, 0 = unlimited

[dispatch_settings]
min_percent = 70  # 0-100
use_aar = false  # experimental AAR API

[mission_filter]
ignore_storm = false
ignore_event = false
min_credits = 0
```

> **Dashboard:** `uvicorn dashboard.app:app --host 127.0.0.1 --port 8000 --reload` then open `http://127.0.0.1:8000` — **everything configurable** (credentials, browser, delays, personnel, alliance, server/region, transport, dispatch, mission filter). No token, local only. Use **Server / Region → Check → Download** for smart per-region `.mscv` sync.

## 🖥️ Usage

Run the bot using Python:

```bash
python Main.py
```

Upon starting, you will be presented with the **V4 Menu**:

```text
╔═══════════════════════════════════╗
║       MISSIONCHIEF BOT MENU       ║
╠═══════════════════════════════════╣
║ 1. Run Missions & Transport [Def] ║
║ 2. Run Missions Only              ║
║ 3. Run Transport Only             ║
║ 4. Exit                           ║
╚═══════════════════════════════════╝
```

  * **Option 1 (Default):** Dedicates one browser thread to Transport logic and the remaining threads to Mission Dispatching. (Requires `browsers = 2` or more in config).
  * **Option 2:** Uses **all** available threads for Mission Dispatching.
  * **Option 3:** Dedicates the browser solely to Transport logic.

### 🎮 Dashboard Bot Control (new — no CLI needed)

Everything can be done from `http://127.0.0.1:8000` → **Bot Control** tab:

* **Start Bot:** choose mode `1/2/3` → **Start Bot** (uses `venv/bin/python` if present, else `python`, feeds menu choice via `stdin`, captures logs)
* **Stop:** `Stop` button → `terminate` → `kill` after 5s
* **Status/Logs:** `GET /api/bot/status` (`running`, `pid`, `mode`, `uptime`) polled every 3s + `GET /api/bot/logs` (last 500, tail 80 shown, `whitespace-pre-wrap`)

API: `POST /api/bot/start {mode:1}`, `POST /api/bot/stop`, `GET /api/bot/status`, `GET /api/bot/logs` — local only, no token.

> **Troubleshooting dashboard launch:** `source venv/bin/activate` before `uvicorn`; `venv/bin/python -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8000 --reload`; if `port busy` `lsof -ti:8000 | xargs kill -9`; if blank page check internet for `tailwindcdn`/`chart.js` CDN.

## 🗺️ Prometheus Development Roadmap

### 🌍 The Big Next Step: International Support

**Multi-Server Support is coming\!**
The biggest focus for upcoming updates is breaking the US-only limitation. We are actively developing a framework to support multiple regions (UK, AU, DE, etc.) out of the box, allowing users worldwide to utilize Prometheus without complex configuration changes.

### ✅ Completed Features

  * **Phase 1: Personnel Management:** Fully implemented. The bot now iterates through buildings and handles hiring based on `personnel_settings`.
  * **Smart Vehicle Logic:** "Water", "Foam", and "Personnel" counting logic is implemented via `vehicle_manager.py` and `.mscv` pattern matching.
  * **Transport Logic:** Basic transport handling (Nearest Hospital/Cell + Prisoner Release) is active.

### 🚧 Upcoming / Planned (Phase 2 & Beyond)

**Advanced Transport & Logistics**

  * **Capacity Limits:** Define maximum patients/prisoners per building to prevent queue overflows.
  * **Distance Limits:** Set max kilometers for transport destinations.
  * **Alliance Buildings:** Settings to toggle usage of alliance hospitals/cells specifically.

**Enhanced Mission Logic**

  * **Event/Alert Filters:** Toggles to specifically ignore Event missions or Storm alerts.
  * **Dynamic Scaling:** Further refinement of vehicle requirements for complex large-scale missions.

**Core System Improvements**

  * **Game Speed Control:** Settings to adjust the simulation speed directly via the bot.

## ⚠️ Disclaimer

This software is for educational purposes only. Using bots or automation tools may violate the Terms of Service of MissionChief/Leitstellenspiel. The developer of Prometheus assumes no responsibility for any bans or penalties applied to your account. Use at your own risk.

-----

**Contact:**
I will update regularly to improve the bot. Please contact me if you need any information about this project:
**Discord:** pouett123456_98797
