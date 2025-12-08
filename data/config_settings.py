import configparser
import os

parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config_path = os.path.join(parent_dir, 'config.ini')
config = configparser.ConfigParser()
config.read(config_path)

# Grabbing Credentials 

def get_username():
    return config.get('credentials', 'username')

def get_password():
    return config.get('credentials', 'password')

# Grabbing Browser Settings 

def get_headless():
    return config.getboolean('browser_settings', 'headless')

def get_threads():
    return config.getint('browser_settings', 'browsers')

# Grabbing Delays 

def get_mission_delay():
    return config.getint('delays', 'missions')

def get_transport_delay():
    return config.getint('delays', 'transport')

# Grabbing Mission Settings (New)

def get_share_alliance():
    try:
        return config.getboolean('mission_settings', 'share_alliance')
    except:
        return True # Default to True if missing

def get_process_alliance():
    try:
        return config.getboolean('mission_settings', 'process_alliance')
    except:
        return True # Default to True if missing