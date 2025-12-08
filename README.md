# Prometheus - MissionChief Bot (US Server)

**Prometheus** is a sophisticated automation bot designed for the US server of the browser game [MissionChief](https://www.missionchief.com).

Built with Python and Playwright, Prometheus is designed to handle mission dispatching, vehicle management, and patient/prisoner transport efficiently. It features a smart vehicle manager that scrapes your current fleet to make intelligent dispatch decisions based on mission requirements.

> **Note:** This project is inspired by [MissionchiefBot-X](https://github.com/NatesHonor/MissionchiefBot-X).

## 🚀 Features

* **Smart Dispatching:** Analyzes mission requirements (including water, foam, and personnel) and selects the appropriate vehicles from your fleet.
* **Transport Logic:** Automatically handles transport requests for ambulances and prisoner transport.
* **Fleet Management:** Scrapes and indexes your personal vehicle IDs to ensure the bot knows exactly what resources are available.
* **Multi-Browser Support:** Configure multiple browser threads to handle high volumes of missions or separate tasks.
* **Mode Selection:** Choose between running Missions & Transport, Missions Only, or Transport Only.
* **Alliance Sharing:** Automatically shares missions with your alliance if configured.
* **Headless Mode:** Run the bot in the background without a visible browser window.

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

## ⚠️ Disclaimer
This software is for educational purposes only. Using bots or automation tools may violate the Terms of Service of MissionChief. The developer of Prometheus assumes no responsibility for any bans or penalties applied to your account. Use at your own risk.
