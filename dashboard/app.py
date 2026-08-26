import asyncio
import collections
import configparser
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

try:
    from utils.remote_vehicle_store import get_asset_status, check_remote_changes, sync_code, get_server_list, get_cache_root
    HAS_REMOTE = True
except Exception:
    HAS_REMOTE = False
    get_asset_status = check_remote_changes = sync_code = get_server_list = get_cache_root = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.ini"
DATA_DIR = PROJECT_ROOT / "data"

# Fallback server list (19 codes) if remote cache missing — matches Server.json
FALLBACK_SERVERS = [
    {"code":"us","url":"https://www.missionchief.com/"},
    {"code":"uk","url":"https://www.missionchief.co.uk/"},
    {"code":"de","url":"https://www.leitstellenspiel.de/"},
    {"code":"fr","url":"https://www.operateur112.fr/"},
    {"code":"nl","url":"https://www.meldkamerspel.com/"},
    {"code":"au","url":"https://www.missionchief-australia.com/"},
    {"code":"cz","url":"https://www.operacni-stredisko.cz/"},
    {"code":"dk","url":"https://www.alarmcentral-spil.dk/"},
    {"code":"fi","url":"https://www.hatakeskuspeli.com/"},
    {"code":"it","url":"https://www.operatore112.it/"},
    {"code":"pl","url":"https://www.operatorratunkowy.pl/"},
    {"code":"pt","url":"https://www.jogo-operador112.com/"},
    {"code":"se","url":"https://www.larmcentralen-spelet.se/"},
    {"code":"no","url":"https://www.nodsentralspillet.com/"},
    {"code":"kr","url":"https://www.missionchief-korea.com/"},
    {"code":"es","url":"https://www.centro-de-mando.es/"},
    {"code":"jp","url":"https://www.missionchief-japan.com/"},
    {"code":"ro","url":"https://www.jocdispecerat112.com/"},
    {"code":"ru","url":"https://www.dispetcher112.ru/"},
]

app = FastAPI(title="Prometheus Dashboard", version="3.1.0", docs_url="/api/docs", redoc_url="/api/redoc")

# Static & templates
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "dashboard" / "static")), name="static")
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "dashboard" / "templates"))

# In-memory stats history for sparkline (keeps last 20 points)
_stats_history = {"credits": [], "missions": []}

def _read_config_dict(redact: bool = False) -> Dict[str, Any]:
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH, encoding="utf-8")
    out = {}
    for section in cfg.sections():
        out[section] = dict(cfg[section])
    # Coerce known sections for UI
    try:
        out.setdefault("browser_settings", {})
        out.setdefault("delays", {})
        out.setdefault("personnel_settings", {})
        out.setdefault("mission_settings", {})
        out.setdefault("credentials", {})
        out.setdefault("server_settings", {})
        out.setdefault("transport_settings", {})
        out.setdefault("dispatch_settings", {})
        out.setdefault("mission_filter", {})
    except Exception:
        pass
    if redact and "credentials" in out and "password" in out["credentials"]:
        # Never leak password — return empty if set
        pw = out["credentials"]["password"]
        out["credentials"]["password"] = "***" if pw else ""
    return out

def _write_config_dict(updates: Dict[str, Any]):
    # Preserve comments & order by editing file in-place instead of ConfigParser.write
    # Fallback to ConfigParser if file missing
    if not CONFIG_PATH.exists():
        cfg = configparser.ConfigParser()
        for section, values in updates.items():
            cfg.add_section(section)
            for k, v in values.items():
                if isinstance(v, bool):
                    v = "true" if v else "false"
                cfg.set(section, k, str(v))
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            cfg.write(f)
        return True

    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
    except Exception:
        text = ""
    lines = text.splitlines()
    # Track section positions
    section_starts = {}
    for idx, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            sec = s[1:-1].strip()
            section_starts[sec] = idx

    for section, values in updates.items():
        if section not in section_starts:
            # Append new section at end
            if lines and lines[-1].strip() != "":
                lines.append("")
            lines.append(f"[{section}]")
            # Recompute section starts after append
            section_starts = {}
            for idx, line in enumerate(lines):
                s = line.strip()
                if s.startswith("[") and s.endswith("]"):
                    sec = s[1:-1].strip()
                    section_starts[sec] = idx

        sec_start = section_starts[section]
        # Find section end (next section start or EOF)
        sec_end = len(lines)
        for sec, pos in section_starts.items():
            if pos > sec_start and pos < sec_end:
                sec_end = pos

        for k, v in values.items():
            if isinstance(v, bool):
                v = "true" if v else "false"
            v = str(v)
            # Search within section
            found = False
            for i in range(sec_start + 1, sec_end):
                stripped = lines[i].strip()
                if not stripped or stripped.startswith("#") or stripped.startswith(";"):
                    continue
                if "=" in lines[i]:
                    key_part = lines[i].split("=", 1)[0].strip()
                    if key_part == k:
                        # Preserve comment after value? Simple replace
                        # Keep leading whitespace
                        indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
                        lines[i] = f"{indent}{k} = {v}"
                        found = True
                        break
            if not found:
                # Insert before section end (before next section or at EOF)
                insert_at = sec_end
                # Find last non-empty within section to insert after
                # Insert at sec_end (which is next section header)
                lines.insert(insert_at, f"{k} = {v}")
                # Update section_starts for sections after
                for sec in section_starts:
                    if section_starts[sec] >= insert_at and sec != section:
                        section_starts[sec] += 1
                sec_end += 1

    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True

def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _get_stats() -> Dict[str, Any]:
    mission_path = DATA_DIR / "mission_data.json"
    vehicle_path = DATA_DIR / "vehicle_data.json"
    mission_data = _load_json(mission_path) or {}
    vehicle_data = _load_json(vehicle_path) or {}

    total_missions = len(mission_data) if isinstance(mission_data, dict) else 0
    total_credits = 0
    water_total = 0
    foam_total = 0
    patients_total = 0
    # credits per mission list
    credits_list = []
    if isinstance(mission_data, dict):
        for mid, m in mission_data.items():
            c = m.get("credits", 0) or 0
            try:
                c = int(c)
            except ValueError:
                c = 0
            total_credits += c
            credits_list.append(c)
            water_total += int(m.get("water_needed", 0) or 0)
            foam_total += int(m.get("foam_needed", 0) or 0)
            patients_total += int(m.get("patients", 0) or 0)

    vehicle_types = len(vehicle_data) if isinstance(vehicle_data, dict) else 0
    total_vehicles = sum(len(v) for v in vehicle_data.values()) if isinstance(vehicle_data, dict) else 0

    # Config snapshot (redacted read but we need raw for stats)
    cfg_dict = _read_config_dict()
    hiring_mode = cfg_dict.get("personnel_settings", {}).get("hiring_mode", "0")
    share_alliance = cfg_dict.get("mission_settings", {}).get("share_alliance", "true")
    process_alliance = cfg_dict.get("mission_settings", {}).get("process_alliance", "true")
    headless = cfg_dict.get("browser_settings", {}).get("headless", "true")
    browsers = cfg_dict.get("browser_settings", {}).get("browsers", "2")
    missions_delay = cfg_dict.get("delays", {}).get("missions", "10")
    transport_delay = cfg_dict.get("delays", {}).get("transport", "60")
    personnel_check = cfg_dict.get("delays", {}).get("personnel_check", "3600")
    server_code = cfg_dict.get("server_settings", {}).get("code", "us")
    server_auto = cfg_dict.get("server_settings", {}).get("auto_update", "true")
    server_interval = cfg_dict.get("server_settings", {}).get("refresh_interval", "3600")
    allow_hosp = cfg_dict.get("transport_settings", {}).get("allow_alliance_hospitals", "true")
    allow_cells = cfg_dict.get("transport_settings", {}).get("allow_alliance_cells", "true")
    max_dist = cfg_dict.get("transport_settings", {}).get("max_distance", "0")
    min_pct = cfg_dict.get("dispatch_settings", {}).get("min_percent", "70")
    use_aar = cfg_dict.get("dispatch_settings", {}).get("use_aar", "false")
    ignore_storm = cfg_dict.get("mission_filter", {}).get("ignore_storm", "false")
    ignore_event = cfg_dict.get("mission_filter", {}).get("ignore_event", "false")
    min_credits = cfg_dict.get("mission_filter", {}).get("min_credits", "0")

    # File mtimes
    def mtime(p: Path):
        try:
            return int(p.stat().st_mtime) if p.exists() else None
        except Exception:
            return None

    # Update sparkline history (keep last 20)
    _stats_history["missions"].append(total_missions)
    _stats_history["credits"].append(total_credits)
    for k in _stats_history:
        if len(_stats_history[k]) > 20:
            _stats_history[k] = _stats_history[k][-20:]

    # Success rate heuristic: missions with vehicles vs total
    # Since we don't have processed count, estimate from credits
    avg_credits = round(total_credits / max(total_missions, 1), 1) if total_missions else 0

    # Asset status for current code
    asset_status = None
    if HAS_REMOTE:
        try:
            from data.config_settings import get_server_cache_dir
            cache_dir = get_server_cache_dir()
            asset_status = get_asset_status(server_code, cache_dir)
        except Exception:
            asset_status = {"code": server_code, "error": "status failed"}

    return {
        "kpis": {
            "missions_pending": total_missions,
            "total_credits": total_credits,
            "avg_credits": avg_credits,
            "vehicle_types": vehicle_types,
            "total_vehicles": total_vehicles,
            "water_needed": water_total,
            "foam_needed": foam_total,
            "patients": patients_total,
        },
        "config": {
            "hiring_mode": hiring_mode,
            "share_alliance": str(share_alliance).lower() == "true",
            "process_alliance": str(process_alliance).lower() == "true",
            "headless": str(headless).lower() == "true",
            "browsers": int(browsers) if str(browsers).isdigit() else browsers,
            "missions_delay": missions_delay,
            "transport_delay": transport_delay,
            "personnel_check": personnel_check,
            "server_code": server_code,
            "server_auto": str(server_auto).lower() == "true",
            "server_interval": server_interval,
            "allow_alliance_hospitals": str(allow_hosp).lower() == "true",
            "allow_alliance_cells": str(allow_cells).lower() == "true",
            "max_distance": int(max_dist) if str(max_dist).isdigit() else 0,
            "min_percent": int(min_pct) if str(min_pct).isdigit() else 70,
            "use_aar": str(use_aar).lower() == "true",
            "ignore_storm": str(ignore_storm).lower() == "true",
            "ignore_event": str(ignore_event).lower() == "true",
            "min_credits": int(min_credits) if str(min_credits).isdigit() else 0,
        },
        "files": {
            "mission_data_mtime": mtime(mission_path),
            "vehicle_data_mtime": mtime(vehicle_path),
        },
        "history": _stats_history,
        "missions": mission_data if total_missions < 200 else {k: mission_data[k] for k in list(mission_data)[:200]},
        "assets": asset_status,
    }

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    try:
        # Starlette 0.47+ wants (request, name, context)
        return templates.TemplateResponse(request, "index.html", {"request": request, "title": "Prometheus Dashboard"})
    except TypeError:
        # Fallback for older Starlette
        return templates.TemplateResponse("index.html", {"request": request, "title": "Prometheus Dashboard"})

@app.get("/api/stats")
async def api_stats():
    try:
        return JSONResponse(_get_stats())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config")
async def api_get_config():
    try:
        # Redact password never leak
        return JSONResponse(_read_config_dict(redact=True))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/config")
async def api_put_config(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Expected JSON object")
        # Validate allowed sections
        allowed_sections = {"credentials", "browser_settings", "personnel_settings", "delays", "mission_settings", "server_settings", "transport_settings", "dispatch_settings", "mission_filter"}
        for sec in body.keys():
            if sec not in allowed_sections:
                raise HTTPException(status_code=400, detail=f"Unknown section: {sec}")
        # If password is "***" (redacted placeholder) treat as no-change
        if "credentials" in body and "password" in body["credentials"]:
            pw = body["credentials"]["password"]
            if pw == "***":
                del body["credentials"]["password"]
                if not body["credentials"]:
                    del body["credentials"]
        # Validate specific fields
        if "browser_settings" in body:
            bs = body["browser_settings"]
            if "browsers" in bs:
                try:
                    b = int(bs["browsers"])
                    if b < 1 or b > 8:
                        raise HTTPException(status_code=400, detail="browsers must be 1-8")
                except ValueError:
                    raise HTTPException(status_code=400, detail="browsers must be integer")
            if "headless" in bs:
                v = str(bs["headless"]).lower()
                if v not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                    raise HTTPException(status_code=400, detail="headless must be boolean")
        if "personnel_settings" in body:
            ps = body["personnel_settings"]
            if "hiring_mode" in ps:
                try:
                    hm = int(ps["hiring_mode"])
                    if hm not in (-1, 0, 1, 2, 3):
                        raise HTTPException(status_code=400, detail="hiring_mode must be -1,0,1,2,3")
                except ValueError:
                    raise HTTPException(status_code=400, detail="hiring_mode must be integer")
        if "mission_settings" in body:
            ms = body["mission_settings"]
            for k in ("share_alliance", "process_alliance"):
                if k in ms:
                    v = str(ms[k]).lower()
                    if v not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                        raise HTTPException(status_code=400, detail=f"{k} must be boolean")
        if "delays" in body:
            for k in ("missions", "transport", "personnel_check"):
                if k in body["delays"]:
                    try:
                        v = int(body["delays"][k])
                        if k == "personnel_check":
                            if v < 600 or v > 86400:
                                raise HTTPException(status_code=400, detail="personnel_check must be 600-86400")
                        elif k == "missions":
                            if v < 3 or v > 300:
                                raise HTTPException(status_code=400, detail="missions must be 3-300")
                        elif k == "transport":
                            if v < 5 or v > 600:
                                raise HTTPException(status_code=400, detail="transport must be 5-600")
                    except ValueError:
                        raise HTTPException(status_code=400, detail=f"{k} must be integer")
        if "server_settings" in body:
            ss = body["server_settings"]
            if "code" in ss:
                c = str(ss["code"]).lower().strip()
                valid = {s["code"] for s in FALLBACK_SERVERS}
                if c not in valid:
                    raise HTTPException(status_code=400, detail=f"code must be one of {', '.join(sorted(valid))}")
            if "auto_update" in ss:
                v = str(ss["auto_update"]).lower()
                if v not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                    raise HTTPException(status_code=400, detail="auto_update must be boolean")
            if "refresh_interval" in ss:
                try:
                    v = int(ss["refresh_interval"])
                    if v < 600 or v > 86400:
                        raise HTTPException(status_code=400, detail="refresh_interval must be 600-86400")
                except ValueError:
                    raise HTTPException(status_code=400, detail="refresh_interval must be integer")
            if "cache_dir" in ss:
                cd = str(ss["cache_dir"]).strip()
                if not cd or "/" in cd or "\\" in cd or ".." in cd:
                    # allow simple dirname like assets_cache or data/cache — but prevent traversal
                    if ".." in cd or cd.startswith("/"):
                        raise HTTPException(status_code=400, detail="cache_dir invalid")
        if "transport_settings" in body:
            ts = body["transport_settings"]
            for k in ("allow_alliance_hospitals", "allow_alliance_cells"):
                if k in ts:
                    v = str(ts[k]).lower()
                    if v not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                        raise HTTPException(status_code=400, detail=f"{k} must be boolean")
            if "max_distance" in ts:
                try:
                    v = int(ts["max_distance"])
                    if v < 0 or v > 1000:
                        raise HTTPException(status_code=400, detail="max_distance must be 0-1000")
                except ValueError:
                    raise HTTPException(status_code=400, detail="max_distance must be integer")
        if "dispatch_settings" in body:
            ds = body["dispatch_settings"]
            if "min_percent" in ds:
                try:
                    v = int(ds["min_percent"])
                    if v < 0 or v > 100:
                        raise HTTPException(status_code=400, detail="min_percent must be 0-100")
                except ValueError:
                    raise HTTPException(status_code=400, detail="min_percent must be integer")
            if "use_aar" in ds:
                v = str(ds["use_aar"]).lower()
                if v not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                    raise HTTPException(status_code=400, detail="use_aar must be boolean")
        if "mission_filter" in body:
            mf = body["mission_filter"]
            for k in ("ignore_storm", "ignore_event"):
                if k in mf:
                    v = str(mf[k]).lower()
                    if v not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                        raise HTTPException(status_code=400, detail=f"{k} must be boolean")
            if "min_credits" in mf:
                try:
                    v = int(mf["min_credits"])
                    if v < 0 or v > 100000:
                        raise HTTPException(status_code=400, detail="min_credits must be 0-100000")
                except ValueError:
                    raise HTTPException(status_code=400, detail="min_credits must be integer")
        _write_config_dict(body)
        # If server code changed, hint to sync — don't auto-sync here, UI will call sync
        return JSONResponse({"status": "ok", "config": _read_config_dict(redact=True)})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/missions")
async def api_missions():
    path = DATA_DIR / "mission_data.json"
    data = _load_json(path)
    if data is None:
        return JSONResponse({"missions": {}, "count": 0, "note": "no data yet — run the bot"})
    return JSONResponse({"missions": data, "count": len(data) if isinstance(data, dict) else 0})

@app.get("/api/vehicles")
async def api_vehicles():
    path = DATA_DIR / "vehicle_data.json"
    data = _load_json(path)
    if data is None:
        return JSONResponse({"vehicles": {}, "count": 0, "note": "no data yet — run the bot"})
    total = sum(len(v) for v in data.values()) if isinstance(data, dict) else 0
    return JSONResponse({"vehicles": data, "types": len(data) if isinstance(data, dict) else 0, "total": total})

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "3.1.0"}

@app.get("/api/servers")
async def api_servers():
    try:
        servers = None
        if HAS_REMOTE:
            # Try cache first
            try:
                from data.config_settings import get_server_cache_dir
                cache_dir = get_server_cache_dir()
                lst = get_server_list(cache_dir)
                if lst:
                    servers = lst
            except Exception:
                servers = None
        if not servers:
            servers = FALLBACK_SERVERS
        # Enrich with current code
        try:
            from data.config_settings import get_server_code
            cur = get_server_code()
        except Exception:
            cur = "us"
        return JSONResponse({"servers": servers, "current": cur, "count": len(servers)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/assets/status")
async def api_assets_status():
    try:
        if not HAS_REMOTE:
            return JSONResponse({"error": "remote module missing (httpx not installed)", "code": "us"})
        from data.config_settings import get_server_code, get_server_cache_dir
        code = get_server_code()
        cache_dir = get_server_cache_dir()
        status = get_asset_status(code, cache_dir)
        # Also check if manifest etag suggests update?
        return JSONResponse(status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/assets/check")
async def api_assets_check():
    try:
        if not HAS_REMOTE:
            raise HTTPException(status_code=500, detail="httpx not installed")
        from data.config_settings import get_manifest_url, get_server_cache_dir, get_server_code
        manifest_url = get_manifest_url()
        cache_dir = get_server_cache_dir()
        code = get_server_code()
        # Check remote changes generically
        res = check_remote_changes(manifest_url, cache_dir)
        # Also status for current code
        status = get_asset_status(code, cache_dir)
        res["code"] = code
        res["cached_files"] = status.get("cached_files")
        res["expected"] = status.get("expected")
        return JSONResponse(res)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/assets/sync")
async def api_assets_sync(request: Request):
    try:
        if not HAS_REMOTE:
            raise HTTPException(status_code=500, detail="httpx not installed — pip install httpx")
        body = {}
        try:
            body = await request.json()
        except Exception:
            body = {}
        # code can be passed explicitly or use config
        code = None
        if isinstance(body, dict):
            code = body.get("code")
        if not code:
            from data.config_settings import get_server_code
            code = get_server_code()
        code = str(code).lower().strip()
        # Validate code
        valid = {s["code"] for s in FALLBACK_SERVERS}
        if code not in valid:
            raise HTTPException(status_code=400, detail=f"Unknown code {code}")
        from data.config_settings import get_server_cache_dir, get_manifest_url, get_server_manifest_url
        cache_dir = get_server_cache_dir()
        manifest_url = get_manifest_url()
        server_manifest_url = get_server_manifest_url()
        # Optional: if body contains code and auto_update, also save code to config
        if isinstance(body, dict) and "code" in body:
            # Persist selection smartly — only if different
            from data.config_settings import get_server_code as gsc
            cur = gsc()
            if cur != code:
                _write_config_dict({"server_settings": {"code": code}})
        # Run sync in thread to avoid blocking event loop (uses ThreadPool internally)
        result = await asyncio.to_thread(sync_code, code, cache_dir, manifest_url, server_manifest_url)
        # After sync, reload VehicleManagers
        try:
            import utils.dispatcher as disp
            import utils.mission_data as md
            if hasattr(disp, "reload_vehicle_manager"):
                disp.reload_vehicle_manager()
            if hasattr(md, "reload_vehicle_manager"):
                md.reload_vehicle_manager()
        except Exception as e:
            result["reload_warning"] = str(e)
        if "error" in result and result.get("fetched", 0) == 0:
            # still return 200 but with error field
            return JSONResponse(result)
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Bot control (subprocess) + persistent logs ---
_bot_process: subprocess.Popen | None = None
_bot_logs: collections.deque = collections.deque(maxlen=500)
_bot_start_time: float | None = None
_bot_mode: str | None = None
_bot_log_file: Path | None = None
_bot_thread: Any = None
import threading
import queue

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def _bot_is_running() -> bool:
    global _bot_process
    if _bot_process is None:
        return False
    return _bot_process.poll() is None

def _bot_drain_thread(proc: subprocess.Popen, log_file: Path):
    """Thread that drains stdout and writes to deque + file (non-blocking)."""
    try:
        # Open file in append
        with open(log_file, "a", encoding="utf-8") as f:
            while True:
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.1)
                    continue
                if isinstance(line, bytes):
                    line = line.decode(errors="ignore")
                line = line.rstrip("\n")
                # Write to file with timestamp
                try:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
                    f.flush()
                except Exception:
                    pass
                _bot_logs.append(line)
                # deque maxlen handles popleft
    except Exception:
        pass

def _bot_read_logs():
    # Legacy drain for fallback — now non-blocking via thread, so just check if thread alive
    # Try to read any pending without blocking (non-thread fallback)
    global _bot_process
    if _bot_process is None or _bot_process.stdout is None:
        return
    # If thread is running, logs are already being drained
    if _bot_thread and _bot_thread.is_alive():
        return
    # Fallback: try non-blocking read via select if available
    try:
        import select
        if hasattr(select, "select"):
            while True:
                rlist, _, _ = select.select([_bot_process.stdout], [], [], 0)
                if not rlist:
                    break
                line = _bot_process.stdout.readline()
                if not line:
                    break
                if isinstance(line, bytes):
                    line = line.decode(errors="ignore")
                _bot_logs.append(line.rstrip())
        else:
            # No select, try one readline with timeout via thread
            pass
    except Exception:
        pass

@app.get("/api/bot/status")
async def api_bot_status():
    running = _bot_is_running()
    pid = _bot_process.pid if _bot_process and running else None
    uptime = int(time.time() - _bot_start_time) if _bot_start_time and running else 0
    return JSONResponse({
        "running": running,
        "pid": pid,
        "mode": _bot_mode if running else None,
        "uptime": uptime,
        "logs_tail": list(_bot_logs)[-20:],
        "log_count": len(_bot_logs)
    })

@app.get("/api/bot/logs")
async def api_bot_logs():
    # Drain any pending output
    _bot_read_logs()
    return JSONResponse({"logs": list(_bot_logs), "count": len(_bot_logs), "running": _bot_is_running()})

@app.post("/api/bot/start")
async def api_bot_start(request: Request):
    global _bot_process, _bot_start_time, _bot_mode, _bot_log_file, _bot_thread
    if _bot_is_running():
        raise HTTPException(status_code=409, detail=f"Bot already running pid={_bot_process.pid} mode={_bot_mode}")
    try:
        body = {}
        try:
            body = await request.json()
        except Exception:
            body = {}
        mode = str(body.get("mode", "1")).strip() or "1"
        if mode not in ("1", "2", "3"):
            raise HTTPException(status_code=400, detail="mode must be 1,2,3")
        venv_python = PROJECT_ROOT / "venv" / "bin" / "python"
        py = str(venv_python) if venv_python.exists() else sys.executable
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        # Log file per run
        _bot_log_file = LOG_DIR / f"bot-{int(time.time())}-mode{mode}.log"
        try:
            _bot_log_file.touch(exist_ok=True)
        except Exception:
            pass
        proc = subprocess.Popen(
            [py, "-u", "Main.py"],
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env
        )
        try:
            proc.stdin.write(mode + "\n")
            proc.stdin.flush()
        except Exception:
            pass
        _bot_process = proc
        _bot_start_time = time.time()
        _bot_mode = mode
        # Start drain thread
        try:
            _bot_thread = threading.Thread(target=_bot_drain_thread, args=(proc, _bot_log_file), daemon=True)
            _bot_thread.start()
        except Exception as e:
            # Fallback without thread
            _bot_thread = None
        await asyncio.sleep(0.7)
        if proc.poll() is not None:
            # Drain any remaining
            _bot_read_logs()
            out = "\n".join(list(_bot_logs)[-30:])
            raise HTTPException(status_code=500, detail=f"Bot exited immediately (code {proc.returncode}): {out[-800:]}")
        # Log start
        try:
            from utils.logger import log_action
            log_action("info", "bot_start", f"Bot started mode {mode} pid {proc.pid}", extra={"mode": mode, "pid": proc.pid})
        except Exception:
            pass
        return JSONResponse({"status": "started", "pid": proc.pid, "mode": mode, "log_file": str(_bot_log_file)})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/bot/stop")
async def api_bot_stop():
    global _bot_process, _bot_start_time, _bot_mode, _bot_log_file, _bot_thread
    if not _bot_is_running():
        return JSONResponse({"status": "not running"})
    try:
        proc = _bot_process
        proc.terminate()
        try:
            await asyncio.to_thread(proc.wait, timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        _bot_read_logs()
        # Give drain thread a moment
        await asyncio.sleep(0.3)
        try:
            from utils.logger import log_action
            log_action("info", "bot_stop", f"Bot stopped pid {proc.pid}", extra={"pid": proc.pid})
        except Exception:
            pass
        _bot_process = None
        _bot_start_time = None
        _bot_mode = None
        _bot_log_file = None
        _bot_thread = None
        return JSONResponse({"status": "stopped"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/logs")
async def api_logs(level: str = None, action: str = None, fix_needed: bool = None, mission_id: str = None, tail: int = 200, search: str = None):
    """Query structured logs from logs/actions.jsonl with filters. Also includes bot logs tail if needed."""
    try:
        from pathlib import Path as _P
        actions_path = PROJECT_ROOT / "logs" / "actions.jsonl"
        prom_path = PROJECT_ROOT / "logs" / "prometheus.log"
        results = []
        # Read actions.jsonl (JSON per line)
        if actions_path.exists():
            try:
                lines = actions_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                # Tail first for performance
                if tail and len(lines) > tail * 3:
                    lines = lines[-tail*3:]
                for line in lines[-5000:]:  # cap
                    line=line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    # Filters
                    if level and obj.get("level","").lower() != level.lower():
                        continue
                    if action and obj.get("action","") != action:
                        continue
                    if fix_needed is not None:
                        # fix_needed param is bool string
                        if isinstance(fix_needed, str):
                            fix_needed = fix_needed.lower() in ("true","1","yes")
                        if bool(obj.get("fix_needed")) != bool(fix_needed):
                            continue
                    if mission_id and str(obj.get("mission_id","")) != str(mission_id):
                        continue
                    if search and search.lower() not in json.dumps(obj).lower() and search.lower() not in obj.get("msg","").lower():
                        continue
                    results.append(obj)
                # Tail
                if tail:
                    results = results[-tail:]
            except Exception as e:
                return JSONResponse({"error": str(e), "logs": []})
        # Also include bot logs tail if requested and no action filter
        bot_logs = list(_bot_logs)[-50:] if not action else []
        return JSONResponse({"logs": results, "count": len(results), "bot_logs": bot_logs, "running": _bot_is_running()})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/logs/fixes")
async def api_logs_fixes(hours: int = 24, tail: int = 100):
    """Aggregated fixes needed in last N hours."""
    try:
        from datetime import datetime, timedelta, timezone
        cutoff = time.time() - hours*3600
        actions_path = PROJECT_ROOT / "logs" / "actions.jsonl"
        fixes = []
        counts: Dict[str, int] = {}
        if actions_path.exists():
            for line in actions_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-5000:]:
                line=line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not obj.get("fix_needed"):
                    continue
                # Parse ts
                ts_str = obj.get("ts","")
                try:
                    # ts is like 2026-08-26T... assume UTC
                    # Use file mtime fallback if parse fails
                    # Simple: check if within hours via log file mtime not precise, so include all recent fix_needed
                    # For now, include all fix_needed and filter by if we can parse
                    if ts_str:
                        # Try to parse ISO
                        try:
                            dt = datetime.fromisoformat(ts_str.replace("Z",""))
                            ts_epoch = dt.timestamp()
                            if ts_epoch < cutoff:
                                continue
                        except Exception:
                            pass
                    fixes.append(obj)
                    act = obj.get("action","general")
                    counts[act] = counts.get(act, 0) + 1
                except Exception:
                    continue
        fixes = fixes[-tail:]
        fixes.reverse()  # newest first
        return JSONResponse({"fixes": fixes, "count": len(fixes), "by_action": counts, "hours": hours})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
