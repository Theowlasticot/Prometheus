"""Single source of truth for Prometheus configuration.

Used by:
  - dashboard/app.py  (validation of PUT /api/config)
  - dashboard UI      (GET /api/config/schema -> form generation)
  - data/config_settings.py (typed getters)

Every key: section, key, type (bool|int|str|password), default, bounds,
choices, and UI metadata (group, label, help).
"""

CONFIG_SCHEMA = [
    # --- server_settings (code + cache_dir handled manually in UI) ---
    {"section": "server_settings", "key": "auto_update", "type": "bool", "default": "true",
     "group": "server", "label": "Auto-update assets",
     "help": "Fetch .mscv updates on dashboard & bot loops"},
    {"section": "server_settings", "key": "refresh_interval", "type": "int", "default": "3600",
     "min": 600, "max": 86400, "group": "server", "label": "Refresh interval (s)",
     "help": "Remote manifest check every N seconds"},

    # --- browser_settings ---
    {"section": "browser_settings", "key": "headless", "type": "bool", "default": "true",
     "group": "browser", "label": "Headless", "help": "Run browsers hidden (recommended)",
     "choices": [("true", "true — hidden (recommended)"), ("false", "false — visible")]},
    {"section": "browser_settings", "key": "browsers", "type": "int", "default": "2",
     "min": 1, "max": 8, "group": "browser", "label": "Browsers",
     "help": "Number of browser threads (1-8)"},

    # --- delays ---
    {"section": "delays", "key": "missions", "type": "int", "default": "10",
     "min": 3, "max": 300, "group": "delays", "label": "Missions (s)",
     "help": "Seconds between mission loops"},
    {"section": "delays", "key": "transport", "type": "int", "default": "60",
     "min": 5, "max": 600, "group": "delays", "label": "Transport (s)",
     "help": "Seconds between transport loops"},
    {"section": "delays", "key": "personnel_check", "type": "int", "default": "3600",
     "min": 600, "max": 86400, "group": "delays", "label": "Personnel check (s)",
     "help": "Seconds between hiring checks"},

    # --- personnel_settings ---
    {"section": "personnel_settings", "key": "hiring_mode", "type": "int", "default": "0",
     "group": "personnel", "label": "Hiring mode",
     "help": "0 Disabled · 1/2/3 days · -1 Automatic (Premium)",
     "choices": [("0", "0 — Disabled"), ("1", "1 — 1 day"), ("2", "2 — 2 days"),
                 ("3", "3 — 3 days"), ("-1", "-1 — Automatic (Premium)")]},

    # --- mission_settings ---
    {"section": "mission_settings", "key": "share_alliance", "type": "bool", "default": "true",
     "group": "mission", "label": "Share alliance", "help": "Auto-share missions to alliance"},
    {"section": "mission_settings", "key": "process_alliance", "type": "bool", "default": "true",
     "group": "mission", "label": "Process alliance", "help": "Include alliance missions"},
    {"section": "mission_settings", "key": "alliance_delay", "type": "int", "default": "45",
     "min": 0, "max": 3600, "group": "mission", "label": "Alliance delay (s)",
     "help": "Grace period before dispatching alliance missions (lets allies respond). 0 = immediate"},

    # --- transport_settings ---
    {"section": "transport_settings", "key": "allow_alliance_hospitals", "type": "bool", "default": "true",
     "group": "transport", "label": "Alliance hospitals", "help": "Use alliance hospital beds"},
    {"section": "transport_settings", "key": "allow_alliance_cells", "type": "bool", "default": "true",
     "group": "transport", "label": "Alliance cells", "help": "Use alliance jail cells"},
    {"section": "transport_settings", "key": "max_distance", "type": "int", "default": "0",
     "min": 0, "max": 1000, "group": "transport", "label": "Max distance (km)",
     "help": "0 = unlimited"},
    {"section": "transport_settings", "key": "alliance_max_tax", "type": "int", "default": "0",
     "min": 0, "max": 100, "group": "transport", "label": "Max alliance tax (%)",
     "help": "Skip alliance hospitals/cells above this tax. 0 = unlimited"},

    # --- dispatch_settings ---
    {"section": "dispatch_settings", "key": "min_percent", "type": "int", "default": "100",
     "min": 0, "max": 100, "group": "dispatch", "label": "Min % to dispatch",
     "help": "Minimum % of requirements available to dispatch (100 recommended with two-stage)"},
    {"section": "dispatch_settings", "key": "use_aar", "type": "bool", "default": "false",
     "group": "dispatch", "label": "Use AAR API",
     "help": "Faster POST /missions/{id}/alarm (experimental)"},
    {"section": "dispatch_settings", "key": "require_training", "type": "bool", "default": "false",
     "group": "dispatch", "label": "Require crew training",
     "help": "Only dispatch specialized vehicles whose crew has the required training"},
    {"section": "dispatch_settings", "key": "lock_ttl", "type": "int", "default": "12",
     "min": 3, "max": 120, "group": "dispatch", "label": "Vehicle lock TTL (s)",
     "help": "In-flight reservation lock duration"},
    {"section": "dispatch_settings", "key": "two_stage", "type": "bool", "default": "true",
     "group": "dispatch", "label": "Two-stage dispatch",
     "help": "Send only 100% requirements; expand on scene arrival"},
    {"section": "dispatch_settings", "key": "max_dispatch_distance", "type": "int", "default": "0",
     "min": 0, "max": 200, "group": "dispatch", "label": "Max dispatch distance (km)",
     "help": "Solver ignores candidates beyond this radius. 0 = unlimited"},
    {"section": "dispatch_settings", "key": "strict_trailer_pairing", "type": "bool", "default": "true",
     "group": "dispatch", "label": "Strict trailer pairing",
     "help": "Require a towing vehicle in the SAME station as the trailer; uncheck otherwise"},

    # --- mission_filter ---
    {"section": "mission_filter", "key": "ignore_storm", "type": "bool", "default": "false",
     "group": "filter", "label": "Ignore storm", "help": "Skip storm missions"},
    {"section": "mission_filter", "key": "ignore_event", "type": "bool", "default": "false",
     "group": "filter", "label": "Ignore event", "help": "Skip event missions"},
    {"section": "mission_filter", "key": "min_credits", "type": "int", "default": "0",
     "min": 0, "max": 100000, "group": "filter", "label": "Min credits",
     "help": "0 = all missions"},

    # --- ingestion_settings (new) ---
    {"section": "ingestion_settings", "key": "api_mode", "type": "str", "default": "auto",
     "group": "ingestion", "label": "Ingestion mode",
     "help": "auto = API v2 with DOM fallback; api_v2 = strict API; dom = legacy HTML scraping",
     "choices": [("auto", "auto — API v2 + DOM fallback (recommended)"),
                 ("api_v2", "api_v2 — strict API"),
                 ("dom", "dom — legacy HTML scraping")]},
    {"section": "ingestion_settings", "key": "crew_scrape", "type": "bool", "default": "true",
     "group": "ingestion", "label": "Scrape crew data",
     "help": "Enrich vehicle data with crew/training at refresh"},

    # --- api_settings (new) ---
    {"section": "api_settings", "key": "min_jitter_ms", "type": "int", "default": "100",
     "min": 0, "max": 5000, "group": "api", "label": "Min jitter (ms)",
     "help": "Minimum humanized delay between API requests"},
    {"section": "api_settings", "key": "max_jitter_ms", "type": "int", "default": "400",
     "min": 0, "max": 5000, "group": "api", "label": "Max jitter (ms)",
     "help": "Maximum humanized delay between API requests"},
    {"section": "api_settings", "key": "max_retries", "type": "int", "default": "3",
     "min": 0, "max": 10, "group": "api", "label": "Max retries",
     "help": "Retry attempts on 429/5xx errors"},
    {"section": "api_settings", "key": "backoff_factor", "type": "float", "default": "1.5",
     "min": 1.0, "max": 5.0, "group": "api", "label": "Backoff factor",
     "help": "Exponential backoff multiplier between retries"},
]

# Ordered card groups for the dashboard Config tab
CONFIG_GROUPS = [
    ("server", "Server Settings", "Remote assets & sync"),
    ("browser", "Browser Settings", "Playwright browsers"),
    ("delays", "Delays", "Loop intervals (seconds)"),
    ("personnel", "Personnel Settings", "Hiring automation"),
    ("mission", "Mission / Alliance", "Sharing behavior"),
    ("transport", "Transport Settings", "Hospital & cell routing"),
    ("dispatch", "Dispatch Settings", "Dispatch engine tuning"),
    ("filter", "Mission Filter", "Ignore rules"),
    ("ingestion", "Ingestion & API", "Data collection"),
    ("api", "API & Network", "Rate limiting, retries"),
]

SECTIONS = sorted({item["section"] for item in CONFIG_SCHEMA})

SECTION_DEFAULTS = {}
for _item in CONFIG_SCHEMA:
    SECTION_DEFAULTS.setdefault(_item["section"], {})[_item["key"]] = _item["default"]


def get_item(section: str, key: str):
    for item in CONFIG_SCHEMA:
        if item["section"] == section and item["key"] == key:
            return item
    return None


def coerce(item, value):
    """Return (ok, coerced_value, error_message)."""
    t = item["type"]
    raw = str(value).strip()
    if t == "bool":
        if raw.lower() in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            return True, raw.lower() in ("true", "1", "yes", "on"), None
        return False, None, f"{item['key']} must be boolean"
    if t == "int":
        try:
            v = int(raw)
        except (TypeError, ValueError):
            return False, None, f"{item['key']} must be integer"
        lo = item.get("min")
        hi = item.get("max")
        if lo is not None and v < lo:
            return False, None, f"{item['key']} must be {lo}-{hi}"
        if hi is not None and v > hi:
            return False, None, f"{item['key']} must be {lo}-{hi}"
        return True, v, None
    if t == "float":
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return False, None, f"{item['key']} must be a number"
        lo = item.get("min")
        hi = item.get("max")
        if lo is not None and v < lo:
            return False, None, f"{item['key']} must be {lo}-{hi}"
        if hi is not None and v > hi:
            return False, None, f"{item['key']} must be {lo}-{hi}"
        return True, v, None
    if t == "str":
        choices = [c[0] for c in item.get("choices", [])]
        if choices and raw not in choices:
            return False, None, f"{item['key']} must be one of {', '.join(choices)}"
        if not raw:
            return False, None, f"{item['key']} must not be empty"
        return True, raw, None
    return True, value, None


def validate_updates(updates):
    """Validate a {section: {key: value}} dict against the schema.

    Returns list of error strings (empty = valid). Coerces values in place.
    """
    errors = []
    for section, values in updates.items():
        if section not in SECTIONS:
            errors.append(f"Unknown section: {section}")
            continue
        for key, value in values.items():
            item = get_item(section, key)
            if item is None:
                errors.append(f"Unknown key: {section}.{key}")
                continue
            ok, coerced, err = coerce(item, value)
            if not ok:
                errors.append(err)
            else:
                values[key] = "true" if coerced is True else (
                    "false" if coerced is False else coerced)
    return errors
