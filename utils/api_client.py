"""Game API client (API v2) via Playwright's APIRequestContext.

`page.context.request` shares the browser session cookies, so no extra auth
is needed. Contracts captured live (tests/recon_live.py):

  GET /api/v2/vehicles  -> {"paging": {count_total, first_id, last_id,
                          next_page}, "result": [{id, building_id,
                          vehicle_type, fms_real, target_type, target_id,
                          assigned_personnel_count, tractive_vehicle_id,
                          custom_water_amount, custom_pump_amount,
                          custom_foam_amount, ...}]}
  GET /api/v2/vehicles?limit=50&after={id}   (next_page cursor)
  GET /api/buildings    -> [{id, building_type, caption, extensions:
                          [{caption, available, enabled, type_id}],
                          patient_count, latitude, longitude, ...}]
  GET /api/vehicles     -> [ ... ] (flat list, no paging wrapper)
  GET /api/missions     -> 404 (mission list stays DOM-scraped)
  GET /api/credits      -> {credits_user_current, user_name, ...}
"""
import asyncio
from data.config_settings import get_server_url, get_api_mode


async def fetch_vehicles_v2(page, limit: int = 200):
    """Full fleet via cursor pagination. Returns list of vehicle dicts."""
    base = get_server_url().rstrip("/")
    req = page.request if hasattr(page, "request") else page.context.request
    url = f"{base}/api/v2/vehicles?limit={limit}"
    vehicles = []
    seen_pages = 0
    while url and seen_pages < 50:
        resp = await req.get(url, timeout=20000)
        if not resp.ok:
            raise RuntimeError(f"vehicles v2 HTTP {resp.status}")
        body = await resp.json()
        result = body.get("result") or []
        vehicles.extend(result)
        seen_pages += 1
        nxt = (body.get("paging") or {}).get("next_page")
        url = nxt if nxt else None
    return vehicles


async def fetch_vehicles_v1(page):
    """Flat vehicle list (no paging wrapper on US server)."""
    base = get_server_url().rstrip("/")
    req = page.request if hasattr(page, "request") else page.context.request
    resp = await req.get(f"{base}/api/vehicles", timeout=20000)
    if not resp.ok:
        raise RuntimeError(f"vehicles v1 HTTP {resp.status}")
    body = await resp.json()
    return body if isinstance(body, list) else []


async def fetch_buildings(page):
    """Building list with extensions + availability flags."""
    base = get_server_url().rstrip("/")
    req = page.request if hasattr(page, "request") else page.context.request
    resp = await req.get(f"{base}/api/buildings", timeout=20000)
    if not resp.ok:
        raise RuntimeError(f"buildings HTTP {resp.status}")
    body = await resp.json()
    return body if isinstance(body, list) else []


def vehicles_to_garage(vehicles):
    """API vehicle list -> garage schema.

    Returns {"by_type": {type_id: [vid]}, "crew": {vid: {personnel, educations}}}.
    Crew counts come from assigned_personnel_count; educations stay empty here
    (enriched by the /zuweisung scrape when crew_scrape is enabled).
    """
    by_type = {}
    crew = {}
    for v in vehicles:
        vid = str(v.get("id"))
        tid = str(v.get("vehicle_type"))
        if not vid or vid == "None":
            continue
        by_type.setdefault(tid, []).append(vid)
        try:
            apc = int(v.get("assigned_personnel_count") or 0)
        except (TypeError, ValueError):
            apc = 0
        crew[vid] = {"personnel": apc, "educations": []}
    return {"by_type": by_type, "crew": crew}


def buildings_to_local(api_buildings):
    """API building list -> building_data.json schema.

    Keeps the legacy {"type", "expansions"} keys (used by has_expansion) and
    adds {"extensions": [{caption, available, enabled, type_id}]} for the
    transport extension/available_at checks.
    """
    out = {}
    for b in api_buildings:
        bid = str(b.get("id"))
        if not bid or bid == "None":
            continue
        exts = b.get("extensions") or []
        expansions = [e.get("caption") for e in exts if e.get("caption")]
        out[bid] = {
            "type": b.get("caption") or b.get("building_type"),
            "expansions": expansions,
            "extensions": [
                {"caption": e.get("caption"), "available": bool(e.get("available")),
                 "enabled": bool(e.get("enabled")), "type_id": e.get("type_id")}
                for e in exts
            ],
            "latitude": b.get("latitude"),
            "longitude": b.get("longitude"),
            "patient_count": b.get("patient_count"),
            "level": b.get("level"),
        }
    return out


async def gather_vehicles_via_api(browser):
    """API v2 first, v1 fallback. Returns garage schema or raises."""
    page = browser.contexts[0].pages[0]
    vehicles = None
    last_err = None
    try:
        vehicles = await fetch_vehicles_v2(page)
    except Exception as e:
        last_err = e
    if not vehicles:
        try:
            vehicles = await fetch_vehicles_v1(page)
        except Exception as e:
            last_err = e
    if not vehicles:
        raise RuntimeError(f"vehicle API empty: {last_err}")
    return vehicles_to_garage(vehicles)
