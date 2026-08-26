import configparser
import os

parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config_path = os.path.join(parent_dir, 'config.ini')
config = configparser.ConfigParser()
config.read(config_path)

def _reload():
    try:
        config.read(config_path, encoding="utf-8")
    except Exception:
        try:
            config.read(config_path)
        except Exception:
            pass

# Grabbing Credentials 

def get_username():
    _reload()
    return config.get('credentials', 'username', fallback="")

def get_password():
    _reload()
    return config.get('credentials', 'password', fallback="")

# Grabbing Browser Settings 

def get_headless():
    _reload()
    return config.getboolean('browser_settings', 'headless', fallback=True)

def get_threads():
    _reload()
    v = config.getint('browser_settings', 'browsers', fallback=2)
    if v < 1: return 1
    if v > 8: return 8
    return v

# Grabbing Delays 

def get_mission_delay():
    _reload()
    v = config.getint('delays', 'missions', fallback=10)
    return max(3, v)

def get_transport_delay():
    _reload()
    v = config.getint('delays', 'transport', fallback=60)
    return max(5, v)

# Grabbing Mission Settings (New)

def get_share_alliance():
    _reload()
    try:
        return config.getboolean('mission_settings', 'share_alliance')
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return True # Default to True if missing

def get_process_alliance():
    _reload()
    try:
        return config.getboolean('mission_settings', 'process_alliance')
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return True # Default to True if missing
def get_hiring_mode():
    _reload()
    try:
        # Returns: 0 (Disabled), 1, 2, 3 (Days), or -1 (Automatic/Premium)
        return config.getint('personnel_settings', 'hiring_mode', fallback=0)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return 0

def get_hiring_check_interval():
    _reload()
    try:
        v = config.getint('delays', 'personnel_check', fallback=3600)
        return max(600, min(v, 86400))
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return 3600

# Server / remote assets

def get_server_code():
    _reload()
    try:
        code = config.get('server_settings', 'code', fallback='us')
        return code.strip().lower() or 'us'
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return 'us'

def get_server_auto_update():
    _reload()
    try:
        return config.getboolean('server_settings', 'auto_update', fallback=True)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return True

def get_server_refresh_interval():
    _reload()
    try:
        v = config.getint('server_settings', 'refresh_interval', fallback=3600)
        return max(600, min(v, 86400))
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return 3600

def get_server_cache_dir():
    _reload()
    try:
        d = config.get('server_settings', 'cache_dir', fallback='assets_cache')
        return d.strip() or 'assets_cache'
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return 'assets_cache'

def get_manifest_url():
    _reload()
    try:
        u = config.get('server_settings', 'manifest_url', fallback='https://raw.githubusercontent.com/cfHxqA/Mission-Chief.Bot/master/Assets.json')
        return u.strip() or 'https://raw.githubusercontent.com/cfHxqA/Mission-Chief.Bot/master/Assets.json'
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return 'https://raw.githubusercontent.com/cfHxqA/Mission-Chief.Bot/master/Assets.json'

def get_server_manifest_url():
    _reload()
    try:
        u = config.get('server_settings', 'server_manifest_url', fallback='https://raw.githubusercontent.com/cfHxqA/Mission-Chief.Bot/master/Assets/Server.json')
        return u.strip() or 'https://raw.githubusercontent.com/cfHxqA/Mission-Chief.Bot/master/Assets/Server.json'
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return 'https://raw.githubusercontent.com/cfHxqA/Mission-Chief.Bot/master/Assets/Server.json'

def get_allow_alliance_hospitals():
    _reload()
    try:
        return config.getboolean('transport_settings', 'allow_alliance_hospitals', fallback=True)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return True

def get_allow_alliance_cells():
    _reload()
    try:
        return config.getboolean('transport_settings', 'allow_alliance_cells', fallback=True)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return True

def get_max_distance():
    _reload()
    try:
        v = config.getint('transport_settings', 'max_distance', fallback=0)
        return max(0, v)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return 0

def get_min_percent():
    _reload()
    try:
        v = config.getint('dispatch_settings', 'min_percent', fallback=70)
        return max(0, min(100, v))
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return 70

def get_use_aar():
    _reload()
    try:
        return config.getboolean('dispatch_settings', 'use_aar', fallback=False)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return False

def get_ignore_storm():
    _reload()
    try:
        return config.getboolean('mission_filter', 'ignore_storm', fallback=False)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return False

def get_ignore_event():
    _reload()
    try:
        return config.getboolean('mission_filter', 'ignore_event', fallback=False)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return False

def get_min_credits():
    _reload()
    try:
        v = config.getint('mission_filter', 'min_credits', fallback=0)
        return max(0, v)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return 0

def get_server_url():
    _reload()
    code = get_server_code()
    mapping = {
        "us": "https://www.missionchief.com/",
        "uk": "https://www.missionchief.co.uk/",
        "de": "https://www.leitstellenspiel.de/",
        "fr": "https://www.operateur112.fr/",
        "nl": "https://www.meldkamerspel.com/",
        "au": "https://www.missionchief-australia.com/",
        "cz": "https://www.operacni-stredisko.cz/",
        "dk": "https://www.alarmcentral-spil.dk/",
        "fi": "https://www.hatakeskuspeli.com/",
        "it": "https://www.operatore112.it/",
        "pl": "https://www.operatorratunkowy.pl/",
        "pt": "https://www.jogo-operador112.com/",
        "se": "https://www.larmcentralen-spelet.se/",
        "no": "https://www.nodsentralspillet.com/",
        "kr": "https://www.missionchief-korea.com/",
        "es": "https://www.centro-de-mando.es/",
        "jp": "https://www.missionchief-japan.com/",
        "ro": "https://www.jocdispecerat112.com/",
        "ru": "https://www.dispetcher112.ru/",
    }
    return mapping.get(code, "https://www.missionchief.com/")

# Language helpers (alliance tags, personnel keywords, distance labels)
ALLIANCE_TAGS = {
    "us": ["[alliance]"], "uk": ["[alliance]"], "au": ["[alliance]"],
    "de": ["[verband]", "[alliance]"], "fr": ["[alliance]", "[alliance]"],
    "nl": ["[alliantie]"], "pl": ["[alliance]"], "it": ["[alliance]"],
    "cz": ["[alliance]"], "dk": ["[alliance]"], "fi": ["[alliance]"],
    "se": ["[alliance]"], "no": ["[alliance]"], "kr": ["[alliance]"],
    "es": ["[alianza]"], "pt": ["[aliança]"], "jp": ["[alliance]"],
    "ro": ["[alliance]"], "ru": ["[alliance]"],
}

def is_alliance_mission_name(name: str) -> bool:
    _reload()
    n = (name or "").lower()
    # Generic: any bracketed prefix likely alliance
    # Check known tags per code plus generic bracket
    code = get_server_code()
    tags = ALLIANCE_TAGS.get(code, ["[alliance]"])
    if any(t in n for t in tags):
        return True
    # Fallback: starts with [ and contains alliance-like word
    if n.strip().startswith("["):
        # consider any [xxx] as alliance if not mission-specific? Conservative: only if length < 30
        if "]" in n[:30]:
            return True
    return False

PERSONNEL_KEYWORDS = ["personnel", "personal", "mitarbeiter", "mitarbeiter", "personnel", "personnel", "personeel", "personel", "personnel", "personnel"]
# Used for i18n detection in personnel manager