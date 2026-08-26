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
        # How often (in seconds) to check personnel. Default 1 hour (3600s)
        return config.getint('delays', 'personnel_check', fallback=3600)
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