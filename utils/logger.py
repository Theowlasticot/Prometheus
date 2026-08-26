import json
import logging
import logging.handlers
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

RUN_ID = str(uuid.uuid4())[:8]

LOG_FILE = LOG_DIR / "prometheus.log"
ACTIONS_FILE = LOG_DIR / "actions.jsonl"

# Ensure files exist
try:
    LOG_FILE.touch(exist_ok=True)
    ACTIONS_FILE.touch(exist_ok=True)
except Exception:
    pass

class JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: Dict[str, Any] = {
            "ts": getattr(record, "ts", time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())),
            "level": record.levelname,
            "msg": record.getMessage(),
            "action": getattr(record, "action", "general"),
            "fix_needed": bool(getattr(record, "fix_needed", False)),
            "run_id": RUN_ID,
        }
        for k in ("mission_id", "vehicle_ids", "thread_id", "loop", "duration_ms", "extra"):
            if hasattr(record, k):
                data[k] = getattr(record, k)
        # Add any extra dict
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            for k, v in extra.items():
                if k not in data:
                    data[k] = v
        return json.dumps(data, ensure_ascii=False)

def get_logger(name: str = "prometheus") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Console handler (color via pretty_print still used separately)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(ch)

    # Rotating file handler for plain log
    try:
        fh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s"))
        logger.addHandler(fh)
    except Exception:
        pass

    # JSONL handler for actions
    try:
        jh = logging.handlers.RotatingFileHandler(
            ACTIONS_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        jh.setLevel(logging.DEBUG)
        jh.setFormatter(JsonlFormatter())
        logger.addHandler(jh)
    except Exception:
        pass

    return logger

_prometheus_logger = get_logger()

def log_action(
    level: str = "info",
    action: str = "general",
    msg: str = "",
    *,
    fix_needed: bool = False,
    mission_id: Optional[str] = None,
    vehicle_ids: Optional[list] = None,
    thread_id: Optional[str] = None,
    loop: Optional[int] = None,
    duration_ms: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
    exc_info: bool = False,
):
    lvl = level.lower()
    # Auto flag fix_needed for warning/error
    if lvl in ("warning", "error", "critical"):
        fix_needed = True
    record_extra = {
        "action": action,
        "fix_needed": fix_needed,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
    }
    if mission_id is not None:
        record_extra["mission_id"] = str(mission_id)
    if vehicle_ids is not None:
        record_extra["vehicle_ids"] = vehicle_ids
    if thread_id is not None:
        record_extra["thread_id"] = str(thread_id)
    if loop is not None:
        record_extra["loop"] = loop
    if duration_ms is not None:
        record_extra["duration_ms"] = duration_ms
    if extra:
        record_extra["extra"] = extra

    # Use logger
    log_fn = getattr(_prometheus_logger, lvl, _prometheus_logger.info)
    try:
        log_fn(msg, extra=record_extra, exc_info=exc_info)
    except Exception:
        # Fallback to print
        print(f"[{lvl.upper()}] {action}: {msg}")

def log_info(action: str, msg: str, **kw): log_action("info", action, msg, **kw)
def log_warning(action: str, msg: str, **kw): log_action("warning", action, msg, **kw)
def log_error(action: str, msg: str, **kw): log_action("error", action, msg, **kw)
def log_debug(action: str, msg: str, **kw): log_action("debug", action, msg, **kw)
