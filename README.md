# Prometheus - MissionChief Bot (US Server)

**Prometheus** is a automation bot designed for the US server of the browser game [MissionChief](https://www.missionchief.com).

Built with Python and Playwright, Prometheus is designed to handle mission dispatching, vehicle management, and patient/prisoner transport efficiently. It features a smart vehicle manager that scrapes your current fleet to make intelligent dispatch decisions based on mission requirements.

> **Note:** This project is inspired by [MissionchiefBot-X](https://github.com/NatesHonor/MissionchiefBot-X).

## 🚀 Features

* **Smart Dispatching:** Analyzes mission requirements (including water, foam, and personnel) and selects the appropriate vehicles from your fleet.
* **Transport Logic:** Automatically handles transport requests for ambulances and prisoner transport.
* **Fleet Management:** Scrapes and indexes your personal vehicle IDs to ensure the bot knows exactly what resources are available.
* **Personnel Management:** Automatically hires new personnel for your stations with configurable hiring durations (1-3 days or Automatic).
* **Alliance Integration:** * **Sharing:** Automatically shares missions with your alliance if configured.
    * **Filtering:** Option to ignore or process alliance missions.
* **Multi-Browser Support:** Configure multiple browser threads to handle high volumes of missions or separate tasks.
* **Mode Selection:** Choose between running Missions & Transport, Missions Only, or Transport Only.
* **Headless Mode:** Run the bot in the background without a visible browser window.
* **Automatic Priority:** Prioritize high earning missions and already running ones.

**⚠️ Note:** This bot is strictly for the **US Server**. Usage on other region servers may require significant modification to the `.mscv` definition files.

## 📋 Prerequisites

* Python 3.8+
* [Playwright](https://playwright.dev/python/)

## 🛠️ Installation

1.  **Clone the repository:**
    ```bash
    git clone [https://github.com/your-username/prometheus.git](https://github.com/your-username/prometheus.git)
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
2.  Fill in your MissionChief credentials and adjust settings:

```ini
[credentials]
username = your_email@example.com
password = your_password

[browser_settings]
# Set to true to hide the browser window
headless = false
# Number of browser threads to launch
browsers = 2

[delays]
# Delay (in seconds) between mission checks
missions = 10
# Delay (in seconds) between transport checks
transport = 20
# Delay (in seconds) between personnel checks (e.g., 3600 = 1 hour)
personnel_check = 3600

[personnel_settings]
# 0: Disabled
# 1, 2, 3: Recruit for X days
# -1: Automatic (Requires Premium)
hiring_mode = 3

[mission_settings]
# Set to false to stop sharing missions with your alliance
share_alliance = true
# Set to false if you want to ignore alliance missions completely
process_alliance = true
````

## ⚠️ Disclaimer

This software is for educational purposes only. Using bots or automation tools may violate the Terms of Service of MissionChief. The developer of Prometheus assumes no responsibility for any bans or penalties applied to your account. Use at your own risk.

PS : I will update regularly to improve the bot, please contact me if you need any information about this project -\> Discord : pouett123456_98797

-----

# 🗺️ Prometheus Development Roadmap

The following features are planned for upcoming releases to enhance the automation, logic, and customization of the bot.

## 👥 Phase 1: Personnel & Management Automation

*Focus on station management and automating daily tasks.*

> **Status:** ✅ Implemented in v2.0

  * **Values/Logic:**
      * **Automatic Hiring:** Enable automated personnel hiring for stations.
      * **Hiring Duration:** Configurable hiring periods:
          * `0`: Disabled (Default)
          * `1-3`: Specific day duration
          * `-1`: Automatic (Premium required)

## 🚛 Phase 2: Advanced Transport & Logistics

*Refining how patients and prisoners are handled to prevent overcrowding and optimize routing.*

  * **Hospital & Cell Logic:**
      * **Capacity Limits:** Define the maximum number of patients/prisoners allowed in a building before skipping to the next available one.
      * **Distance Limits:** Set a maximum distance (km) for transporting patients or prisoners to a destination.
  * **Alliance Integration:**
      * **Alliance Buildings:** Toggle support for transporting to alliance hospitals/cells.
      * **Alliance Distance:** Separate maximum distance setting for alliance buildings.
  * **Task Management:**
      * **Speech Task Interval:** Configurable delay (default: 3s) between handling specific transport "speech" requests.
      * **Toggle Speech Handling:** Option to enable/disable processing of transport requests entirely.

## 🧩 Phase 3: Enhanced Mission Logic

*Smarter dispatching decisions and finer control over what missions are run.*

  * **Mission Filters:**
      * **Event Missions:** Toggle to enable/disable processing of event missions (default: enabled).
      * **Alert Missions:** Toggle to enable/disable (post-)alert missions (default: enabled).
      * **Distance Caps:** Set maximum dispatch distance for (post-)alert missions.
      * **Re-open Missions:** Option to force re-opening of missions regardless of their state (default: disabled).
  * **Smart Dispatching:**
      * **Dynamic Scaling:** Automatically adjust required vehicles based on water/personnel/patient needs (default: enabled).
      * **Personnel Availability:** Check if vehicles have enough trained personnel ready before dispatching; disable vehicle if insufficient (default: disabled).
      * **Alliance Fleet:** Option to "Consider Vehicles from Members" when calculating requirements (default: disabled).

## 🤝 Phase 4: Alliance Collaboration

*Improved sharing logic to support alliance play styles.*

  * **Conditional Sharing:**
      * **Share on Transport:** Automatically share missions if a patient or prisoner transport is requested.
      * **Share on Missing:** Automatically share missions if your fleet lacks the required vehicles.
      * **General Sharing:** Master toggle to share all processed missions.

## ⚙️ Phase 5: Core System Improvements

*Quality of life updates for the bot's operation.*

  * **Game Speed Control:** New setting to adjust the simulation/action speed:
      * `0`: Pause
      * `1`: Turbo
      * `...`
      * `8`: Extreme Slow
