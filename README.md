# Prometheus - MissionChief Bot (US Server) - V3

**Prometheus V3** is an advanced automation bot designed for the US server of the browser game [MissionChief](https://www.missionchief.com).

Built with Python and Playwright, Prometheus handles mission dispatching, intelligent fleet management, prisoner/patient transport, and station personnel recruitment. V3 introduces a robust **CLI Menu System**, allowing you to choose specifically between mission dispatching, transport logic, or running both simultaneously across multiple browser threads.

> **Note:** This project is inspired by [MissionchiefBot-X](https://github.com/NatesHonor/MissionchiefBot-X).

## 🚀 Key Features (V3)

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

**⚠️ Region Warning:** Currently, this bot is optimized for the **US Server**. Usage on other region servers will require modifying the `.mscv` definition files in the `us/` folder. **However, international support is our top priority for the next update (see Roadmap).**

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

2.  **Install dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

3.  **Install Playwright browsers:**

    ```bash
    playwright install
    ```

## ⚙️ Configuration

1.  Open `config.ini` in the root directory.
2.  Fill in your MissionChief credentials and adjust the settings. Below is the configuration structure for **V3**:

<!-- end list -->

```ini
[credentials]
username = your_email@example.com
password = your_password

[browser_settings]
# Set to true to hide the browser window (run in background)
headless = true
# Number of browser threads to launch
browsers = 2

[personnel_settings]
# 0: Disabled
# 1, 2, 3: Recruit for X days
# -1: Automatic (Requires Premium)
hiring_mode = 3

[delays]
# Delay (in seconds) between mission checks
missions = 10
# Delay (in seconds) between transport checks
transport = 20
# Delay (in seconds) between personnel checks (e.g., 3600 = 1 hour)
personnel_check = 3600

[mission_settings]
# Set to false to stop sharing missions with your alliance
share_alliance = true
# Set to false if you want to ignore alliance missions completely
process_alliance = true
```

## 🖥️ Usage

Run the bot using Python:

```bash
python Main.py
```

Upon starting, you will be presented with the **V3 Menu**:

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
