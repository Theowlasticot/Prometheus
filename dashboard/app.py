import configparser
import json
import os
from pathlib import Path
from typing import Dict, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.ini"
DATA_DIR = PROJECT_ROOT / "data"

app = FastAPI(title="Prometheus Dashboard", version="3.1.0", docs_url="/api/docs", redoc_url="/api/redoc")

# Static & templates
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "dashboard" / "static")), name="static")
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "dashboard" / "templates"))

# In-memory stats history for sparkline (keeps last 20 points)
_stats_history = {"credits": [], "missions": []}

def _read_config_dict() -> Dict[str, Any]:
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH, encoding="utf-8")
    out = {}
    for section in cfg.sections():
        out[section] = dict(cfg[section])
    # Coerce known booleans/ints for UI
    try:
        out.setdefault("browser_settings", {})
        out.setdefault("delays", {})
        out.setdefault("personnel_settings", {})
        out.setdefault("mission_settings", {})
        out.setdefault("credentials", {})
    except Exception:
        pass
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
            section_starts[section] = len(lines) - 1
            # Also need to update subsequent section starts after append
            # Recompute quickly
            for sec, pos in list(section_starts.items()):
                if sec != section and pos > section_starts[section]:
                    section_starts[sec] += 2

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

    # Config snapshot
    cfg = _load_json(CONFIG_PATH)  # not json, fallback
    # Use configparser for actual values
    cfg_dict = _read_config_dict()
    hiring_mode = cfg_dict.get("personnel_settings", {}).get("hiring_mode", "0")
    share_alliance = cfg_dict.get("mission_settings", {}).get("share_alliance", "true")
    process_alliance = cfg_dict.get("mission_settings", {}).get("process_alliance", "true")
    headless = cfg_dict.get("browser_settings", {}).get("headless", "true")
    browsers = cfg_dict.get("browser_settings", {}).get("browsers", "2")
    missions_delay = cfg_dict.get("delays", {}).get("missions", "10")
    transport_delay = cfg_dict.get("delays", {}).get("transport", "60")
    personnel_check = cfg_dict.get("delays", {}).get("personnel_check", "3600")

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
        },
        "files": {
            "mission_data_mtime": mtime(mission_path),
            "vehicle_data_mtime": mtime(vehicle_path),
        },
        "history": _stats_history,
        "missions": mission_data if total_missions < 200 else {k: mission_data[k] for k in list(mission_data)[:200]},
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
        return JSONResponse(_read_config_dict())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/config")
async def api_put_config(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Expected JSON object")
        # Validate allowed sections
        allowed_sections = {"credentials", "browser_settings", "personnel_settings", "delays", "mission_settings"}
        for sec in body.keys():
            if sec not in allowed_sections:
                raise HTTPException(status_code=400, detail=f"Unknown section: {sec}")
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
        # Redact credentials from logging? Keep but don't echo password in response fully
        _write_config_dict(body)
        return JSONResponse({"status": "ok", "config": _read_config_dict()})
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
