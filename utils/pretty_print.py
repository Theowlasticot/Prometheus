import art

try:
    from utils.logger import log_action
    _HAS_LOGGER = True
except Exception:
    _HAS_LOGGER = False

def display_message(message):
    ascii_art = art.text2art(message)
    print(ascii_art)
    if _HAS_LOGGER:
        try:
            log_action("info", "banner", message)
        except Exception:
            pass

def display_error(message):
    print(f"\033[91m{message}\033[0m")
    if _HAS_LOGGER:
        try:
            log_action("error", "error", message, fix_needed=True)
        except Exception:
            pass

def display_warning(message):
    print(f"\033[93m{message}\033[0m")
    if _HAS_LOGGER:
        try:
            log_action("warning", "warning", message, fix_needed=True)
        except Exception:
            pass

def display_info(message):
    print(f"{message}")
    if _HAS_LOGGER:
        try:
            log_action("info", "general", message)
        except Exception:
            pass

def display_debug(message):
    print(f"\033[90m{message}\033[0m")
    if _HAS_LOGGER:
        try:
            log_action("debug", "debug", message)
        except Exception:
            pass
