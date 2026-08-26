import json
import asyncio
import os
from pathlib import Path

from utils.pretty_print import display_info, display_error, display_warning
from utils.vehicle_manager import VehicleManager
from data.config_settings import get_share_alliance, get_process_alliance, get_server_code, get_server_url, is_alliance_mission_name, get_min_percent, get_use_aar, get_ignore_storm, get_ignore_event, get_min_credits
from utils.building_data import load_building_data, has_expansion

# Trailer types that require towing vehicle (cannot dispatch alone)
TRAILER_IDS = {7, 31, 35, 36, 37, 38, 41, 46, 59}  # water trailer, foam trailer, etc. — approximate US
# Vehicle locking to avoid double-dispatch across missions (inspired by NatesHonor)
_LOCKED_VEHICLES: dict[str, str] = {}  # vehicle_id -> mission_id

def is_vehicle_locked(vid: str) -> bool:
    return vid in _LOCKED_VEHICLES

def lock_vehicle(vid: str, mission_id: str):
    _LOCKED_VEHICLES[vid] = mission_id

def free_up_vehicles(mission_id: str):
    to_free = [vid for vid, mid in _LOCKED_VEHICLES.items() if mid == mission_id]
    for vid in to_free:
        del _LOCKED_VEHICLES[vid]

def free_all_vehicles():
    _LOCKED_VEHICLES.clear()

async def get_vehicle_distances(page, vehicle_ids: list[str]) -> dict[str, float]:
    """Read #vehicle_sort_{id} sortvalue as distance, like NatesHonor vehicles.py:8."""
    distances = {}
    for vid in vehicle_ids:
        try:
            # Try sortvalue attribute
            el = await page.query_selector(f'#vehicle_sort_{vid}')
            if el:
                val = await el.get_attribute('sortvalue')
                if val is not None:
                    try:
                        distances[vid] = float(val.replace(',', '.'))
                        continue
                    except ValueError:
                        pass
            # Fallback: try data attribute or text
            distances[vid] = float('inf')
        except Exception:
            distances[vid] = float('inf')
    return distances

# Dynamic manager — code from config, cache-aware (assets_cache/{code} or bundled us)
def _create_manager():
    try:
        code = get_server_code()
    except Exception:
        code = "us"
    return VehicleManager(code=code)

VEHICLE_MANAGER = _create_manager()

def reload_vehicle_manager():
    global VEHICLE_MANAGER
    VEHICLE_MANAGER = _create_manager()
    display_info(f"VehicleManager reloaded for code={VEHICLE_MANAGER.code} from {VEHICLE_MANAGER.data_folder}")
    return VEHICLE_MANAGER
VEHICLE_DATA_CACHE = None
USER_TO_SYSTEM_MAP = {} 

PROJECT_ROOT = Path(__file__).resolve().parent.parent

async def load_vehicle_data(force=False):
    global VEHICLE_DATA_CACHE, USER_TO_SYSTEM_MAP
    if VEHICLE_DATA_CACHE is None or force:
        try:
            with open(PROJECT_ROOT / 'data' / 'vehicle_data.json', 'r') as file:
                VEHICLE_DATA_CACHE = json.load(file)
            
            # Build Reverse Map for Intelligent Logic
            USER_TO_SYSTEM_MAP = {}
            for sys_id, user_ids in VEHICLE_DATA_CACHE.items():
                for uid in user_ids:
                    USER_TO_SYSTEM_MAP[str(uid)] = int(sys_id)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            display_warning(f"Vehicle data load failed: {e}")
            VEHICLE_DATA_CACHE = {}
            USER_TO_SYSTEM_MAP = {}
        except asyncio.CancelledError:
            raise
    return VEHICLE_DATA_CACHE

async def navigate_and_dispatch(browsers):
    try:
        with open(PROJECT_ROOT / 'data' / 'mission_data.json', 'r') as file:
            mission_data = json.load(file)
    except FileNotFoundError:
        display_error("mission_data.json not found.")
        return
    except (OSError, json.JSONDecodeError) as e:
        display_error(f"mission_data.json unreadable: {e}")
        return

    await load_vehicle_data(force=True)
    page = browsers[0].contexts[0].pages[0]

    # Apply mission filters (storm/event/min_credits) before sorting
    try:
        ignore_storm = get_ignore_storm()
        ignore_event = get_ignore_event()
        min_credits = get_min_credits()
    except Exception:
        ignore_storm = False
        ignore_event = False
        min_credits = 0
    filtered = {}
    for mid, data in mission_data.items():
        name = data.get("mission_name", "").lower()
        credits = data.get("credits", 0) or 0
        if ignore_storm and "storm" in name:
            continue
        if ignore_event and any(x in name for x in ["event", "storm surge", "civil unrest"]):
            continue
        if min_credits and credits < min_credits:
            continue
        filtered[mid] = data
    if len(filtered) != len(mission_data):
        display_info(f"Filtered {len(mission_data)-len(filtered)} missions (storm/event/credits)")
        mission_data = filtered

    # Sort by missing first, then credits per vehicle (more efficient), then credits
    def _sort_key(item):
        name = item[1].get("mission_name", "").lower()
        is_missing = 1 if any(x in name for x in ["missing", "incomplete", "fehl", "unvollständig", "manquant"]) else 0
        credits = item[1].get("credits", 0) or 0
        vehicles = item[1].get("vehicles", [])
        total_needed = sum(v.get("count",0) for v in vehicles) or 1
        credits_per = credits / total_needed if total_needed else credits
        return (is_missing, credits_per, credits)

    sorted_missions = sorted(
        mission_data.items(),
        key=_sort_key,
        reverse=True
    )

    display_info(f"Loaded {len(sorted_missions)} missions. Processing...")

    for mission_id, data in sorted_missions:
        mission_name = data.get("mission_name", "Unknown Mission")
        credits_val = data.get("credits", 0)
        crashed_cars = data.get("crashed_cars", 0)
        req_water = data.get("water_needed", 0)
        req_foam = data.get("foam_needed", 0)
        patients_count = data.get("patients", 0)

        # i18n: missing/incomplete detection (en/de/fr/nl...)
        name_lower = mission_name.lower()
        is_missing_mission = any(x in name_lower for x in ["missing", "incomplete", "fehl", "unvollständig", "manquant", "incomplet"])
        is_alliance_mission = is_alliance_mission_name(mission_name)

        if is_alliance_mission and not get_process_alliance():
            display_info(f"⏭️ Skipping Alliance Mission: {mission_name}")
            continue

        display_info(f"Checking mission: {mission_name} ({credits_val} Cr) (ID: {mission_id})")

        try:
            base = get_server_url().rstrip("/")
            await page.goto(f"{base}/missions/{mission_id}", timeout=30000)
            await page.wait_for_selector('#missionH1', timeout=5000)
        except Exception as e:
            display_error(f"Mission {mission_id} failed to load: {e}")
            continue

        if is_missing_mission or is_alliance_mission:
            is_doable = True
            reason = "Force Dispatch (Alliance or Incomplete)"
        else:
            is_doable, reason = await check_mission_requirements_global_percent(page, data)

        if not is_doable:
            display_info(f"⏭️ SKIPPING {mission_id} (Not Shared): {reason}")
            continue
            
        if get_share_alliance():
            try:
                share_btn = await page.query_selector('#mission_alliance_share_btn')
                if share_btn and await share_btn.is_visible():
                    await share_btn.click()
                    display_info(f"🤝 Shared mission {mission_id}.")
                    await page.wait_for_timeout(500)
            except Exception as e:
                display_warning(f"Share button error {mission_id}: {e}")

        display_info(f"✅ Dispatching: {reason}")

        try:
            load_btn = await page.query_selector('a.missing_vehicles_load.btn-warning')
            if load_btn:
                await load_btn.click()
                await page.wait_for_load_state('networkidle')
                await page.wait_for_timeout(1000)
        except Exception as e:
            display_warning(f"Load missing vehicles error {mission_id}: {e}")

        # --- SELECT VEHICLES ---
        vehicle_requirements = data.get("vehicles", [])
        # Filter S5 / disabled vehicles — only available, visible, not disabled
        available_vehicles_elements = []
        try:
            all_cbs = await page.query_selector_all('input.vehicle_checkbox')
            for cb in all_cbs:
                try:
                    if not await cb.is_visible():
                        continue
                    # Check disabled attribute
                    dis = await cb.get_attribute("disabled")
                    if dis is not None:
                        continue
                    # Check parent row disabled class
                    is_disabled = await cb.is_disabled() if hasattr(cb, "is_disabled") else False
                    if is_disabled:
                        continue
                    available_vehicles_elements.append(cb)
                except Exception:
                    # Fallback: add if no exception
                    try:
                        if await cb.is_visible():
                            available_vehicles_elements.append(cb)
                    except Exception:
                        continue
            if not available_vehicles_elements:
                # Fallback to original visible selector
                available_vehicles_elements = await page.query_selector_all('input.vehicle_checkbox:visible')
        except Exception:
            available_vehicles_elements = await page.query_selector_all('input.vehicle_checkbox')
        used_vehicle_ids = []
        
        current_water = 0
        current_foam = 0

        # Building expansion gating (if mission_data captured it)
        required_exps = data.get("required_expansions", [])
        if required_exps:
            bdata = load_building_data()
            missing_exp = [e for e in required_exps if not has_expansion(bdata, e)]
            if missing_exp:
                display_warning(f"Skipping {mission_id} missing expansions {missing_exp}")
                continue

        for requirement in vehicle_requirements:
            req_name = requirement["name"]
            req_count = requirement["count"]
            
            if "ambulance" in req_name.lower(): continue

            valid_ids = await get_valid_ids_for_type(req_name)
            # Distance sort: nearest first (like NatesHonor vehicles.py)
            # Build distance map for valid_ids
            try:
                dist_map = await get_vehicle_distances(page, valid_ids)
                # Sort valid_ids by distance
                valid_ids_sorted = sorted(valid_ids, key=lambda vid: dist_map.get(vid, float('inf')))
            except Exception:
                valid_ids_sorted = valid_ids
            # Also sort available elements by distance for this requirement
            # Create distance-sorted list of (cb, v_id)
            sorted_cbs = []
            for cb in available_vehicles_elements:
                try:
                    v_id_tmp = await cb.get_attribute("value")
                    if v_id_tmp in valid_ids:
                        d = dist_map.get(v_id_tmp, float('inf')) if 'dist_map' in locals() else float('inf')
                        sorted_cbs.append((d, cb, v_id_tmp))
                except Exception:
                    continue
            sorted_cbs.sort(key=lambda x: x[0])
            selected = 0
            
            for _, cb, v_id in sorted_cbs:
                try:
                    is_checked = await cb.is_checked()
                except Exception:
                    is_checked = False
                
                if v_id in used_vehicle_ids or is_checked or is_vehicle_locked(v_id):
                    if is_checked and v_id not in used_vehicle_ids:
                        used_vehicle_ids.append(v_id)
                        lock_vehicle(v_id, mission_id)
                    continue
                
                if v_id in valid_ids_sorted:
                    # SMART QUANTITY LOGIC
                    sys_id = USER_TO_SYSTEM_MAP.get(str(v_id))
                    
                    # Dynamic Target Calculation (Regex Match)
                    current_target = req_count
                    if sys_id:
                        current_target = VEHICLE_MANAGER.get_required_quantity(sys_id, req_name, req_count)
                    
                    if selected >= current_target: 
                        break

                    await click_vehicle(page, cb)
                    used_vehicle_ids.append(v_id)
                    lock_vehicle(v_id, mission_id)
                    
                    # Fix: support both US (water_amount) and German (wasser_amount) attributes
                    w_raw = await cb.get_attribute("water_amount") or await cb.get_attribute("wasser_amount") or "0"
                    f_raw = await cb.get_attribute("foam_amount") or await cb.get_attribute("foam_amount_display") or "0"
                    try:
                        w = int(str(w_raw).replace(',', '').strip() or 0)
                    except ValueError:
                        w = 0
                    try:
                        f = int(str(f_raw).replace(',', '').strip() or 0)
                    except ValueError:
                        f = 0
                    current_water += w
                    current_foam += f
                    
                    display_info(f"Selected {req_name} (ID: {v_id}) [Target: {current_target}]")
                    selected += 1

        # --- AMBULANCES ---
        if patients_count > 0:
            ambulance_ids = await get_valid_ids_for_type("ambulance")
            amb_req = next((r for r in vehicle_requirements if "ambulance" in r["name"].lower()), None)
            count_to_send = amb_req["count"] if amb_req else patients_count
            
            ambulances_sent = 0
            for cb in available_vehicles_elements:
                if ambulances_sent >= count_to_send: break
                v_id = await cb.get_attribute("value")
                if v_id in used_vehicle_ids or await cb.is_checked(): continue
                
                if v_id in ambulance_ids:
                    await click_vehicle(page, cb)
                    used_vehicle_ids.append(v_id)
                    display_info(f"Selected ambulance (ID: {v_id})")
                    ambulances_sent += 1

        # --- RESOURCES (Capability Optimized) ---
        if req_water > current_water or req_foam > current_foam:
            potential_foam_ids = VEHICLE_MANAGER.get_ids_with_capability("FOAM")
            potential_water_ids = VEHICLE_MANAGER.get_ids_with_capability("WATER")
            
            remaining = await page.query_selector_all('input.vehicle_checkbox:not(:checked)')
            for cb in remaining:
                if current_water >= req_water and current_foam >= req_foam: break
                vid = await cb.get_attribute("value")
                if vid in used_vehicle_ids: continue
                
                sys_id = USER_TO_SYSTEM_MAP.get(str(vid))
                if not sys_id: continue 
                
                # Check Capabilities in DB first to save time
                needs_check = False
                if req_foam > current_foam and sys_id in potential_foam_ids:
                    needs_check = True
                if req_water > current_water and sys_id in potential_water_ids:
                    needs_check = True
                    
                if not needs_check: continue
                
                w_raw = await cb.get_attribute("water_amount") or await cb.get_attribute("wasser_amount") or "0"
                f_raw = await cb.get_attribute("foam_amount") or await cb.get_attribute("foam_amount_display") or "0"
                try:
                    w = int(str(w_raw).replace(',', '').strip() or 0)
                except ValueError:
                    w = 0
                try:
                    f = int(str(f_raw).replace(',', '').strip() or 0)
                except ValueError:
                    f = 0
                
                useful = False
                if req_water > current_water and w > 0:
                    current_water += w
                    useful = True
                if req_foam > current_foam and f > 0:
                    current_foam += f
                    useful = True
                
                if useful:
                    await click_vehicle(page, cb)
                    used_vehicle_ids.append(vid)
                    display_info(f"Resource Vehicle ({vid}): +{w}W / +{f}F")

        # --- TRAILER TOWING CHECK ---
        # Ensure trailers have towing vehicles (Heavy Rescue, Utility, etc.)
        try:
            trailer_used = []
            for vid in list(used_vehicle_ids):
                sys_id = USER_TO_SYSTEM_MAP.get(str(vid))
                if sys_id in TRAILER_IDS:
                    trailer_used.append(vid)
            if trailer_used:
                # Need towing vehicle for each trailer — try to find one
                tow_needed = len(trailer_used)
                tow_found = 0
                # Towing vehicles are typically Heavy Rescue, Utility, Battalion, etc. (not trailers)
                for cb in available_vehicles_elements:
                    if tow_found >= tow_needed:
                        break
                    vid = await cb.get_attribute("value")
                    if vid in used_vehicle_ids or await cb.is_checked():
                        continue
                    sys_id = USER_TO_SYSTEM_MAP.get(str(vid))
                    if sys_id and sys_id not in TRAILER_IDS:
                        # Check if this vehicle can tow (heuristic: heavy rescue/utility/battalion)
                        # For now accept any non-trailer that is valid for rescue/utility
                        await click_vehicle(page, cb)
                        used_vehicle_ids.append(vid)
                        tow_found += 1
                        display_info(f"Towing vehicle for trailer: {vid}")
                if tow_found < tow_needed:
                    display_warning(f"Trailer(s) {trailer_used} may lack towing vehicle ({tow_found}/{tow_needed})")
        except Exception as e:
            display_warning(f"Trailer check error: {e}")

        # --- SEND ---
        # Try AAR API first if enabled (faster, avoids checkbox flakiness), else click button
        dispatched = False
        if get_use_aar():
            try:
                base = get_server_url().rstrip("/")
                # Use Playwright APIRequestContext via page.request
                # POST to /missions/{id}/alarm with vehicle_ids[]
                payload = {"vehicle_ids[]": used_vehicle_ids, "next_mission": "0"}
                # page.request is available on page.context.request or page.request
                req_ctx = page.request if hasattr(page, "request") else page.context.request
                resp = await req_ctx.post(f"{base}/missions/{mission_id}/alarm", form=payload)
                if resp.ok:
                    display_info(f"🚀 Dispatched via AAR API {mission_id} ({len(used_vehicle_ids)} vehicles)")
                    dispatched = True
                else:
                    display_warning(f"AAR dispatch failed {resp.status}: {await resp.text()[:200]} — falling back to click")
            except Exception as e:
                display_warning(f"AAR error {mission_id}: {e}")

        if not dispatched:
            btn = await page.query_selector('#alert_btn')
            if btn:
                if len(used_vehicle_ids) == 0:
                    display_info(f"⛔ No vehicles selected for {mission_id}. Skipping dispatch click.")
                    continue
                try:
                    is_disabled = await btn.get_attribute("disabled")
                    if is_disabled is not None:
                        display_warning(f"Dispatch button disabled for {mission_id}")
                        continue
                except Exception:
                    pass
                try:
                    await btn.scroll_into_view_if_needed()
                except Exception:
                    await page.evaluate('(btn) => btn.scrollIntoView()', btn)
                await btn.click()
                # Verify dispatch succeeded (check for success alert or mission gone)
                try:
                    await page.wait_for_timeout(800)
                except Exception:
                    pass
                display_info(f"🚀 Dispatched mission {mission_id} via click ({len(used_vehicle_ids)} vehicles)")
            else:
                display_warning(f"No dispatch button for {mission_id}")

async def check_mission_requirements_global_percent(page, mission_data):
    # Use same visible+enabled filter as dispatch
    try:
        all_cbs = await page.query_selector_all('input.vehicle_checkbox')
        checkboxes = []
        for cb in all_cbs:
            try:
                if not await cb.is_visible():
                    continue
                dis = await cb.get_attribute("disabled")
                if dis is not None:
                    continue
                if hasattr(cb, "is_disabled") and await cb.is_disabled():
                    continue
                checkboxes.append(cb)
            except Exception:
                continue
        if not checkboxes:
            checkboxes = await page.query_selector_all('input.vehicle_checkbox:visible')
    except Exception:
        checkboxes = await page.query_selector_all('input.vehicle_checkbox:visible')
    available_ids_pool = []
    for cb in checkboxes:
        try:
            v = await cb.get_attribute("value")
            if v: available_ids_pool.append(v)
        except Exception:
            continue
    
    vehicle_requirements = mission_data.get("vehicles", [])
    total_needed = 0
    total_found = 0
    
    simulation_pool = available_ids_pool.copy()
    IGNORED = ['ambulance', 'ems', 'patient']

    for req in vehicle_requirements:
        req_name = req["name"]
        req_count = req["count"]
        
        if any(k in req_name.lower() for k in IGNORED):
            continue
            
        total_needed += req_count
        valid_ids = await get_valid_ids_for_type(req_name)
        
        found = 0
        to_remove = []
        for vid in simulation_pool:
            if vid in valid_ids:
                found += 1
                to_remove.append(vid)
                if found >= req_count: break
        
        total_found += min(found, req_count)
        for vid in to_remove: simulation_pool.remove(vid)

    if total_needed == 0:
        return True, "Only EMS/Transport needed"

    # Configurable threshold (default 70)
    try:
        min_pct = get_min_percent()
    except Exception:
        min_pct = 70
    ratio = min_pct / 100
    if total_found == total_needed:
        return True, f"Full Match: {total_found}/{total_needed}"
    if total_found / max(total_needed, 1) >= ratio:
        return True, f"Partial Match: {total_found}/{total_needed} (>={min_pct}%)"
    if total_found > 0:
        display_warning(f"Skipping — only {total_found}/{total_needed} vehicles available (<{min_pct}%)")
    
    return False, f"Insufficient: {total_found}/{total_needed} vehicles found."

async def click_vehicle(page, checkbox):
    await page.evaluate('(checkbox) => checkbox.scrollIntoView()', checkbox)
    await page.evaluate('(checkbox) => { checkbox.click(); checkbox.dispatchEvent(new Event("change", { bubbles: true })); }', checkbox)

async def get_valid_ids_for_type(target_name):
    user_vehicle_data = await load_vehicle_data() 
    allowed_generic_ids = VEHICLE_MANAGER.get_valid_ids(target_name)
    
    valid_ids_in_garage = []
    for allowed_id in allowed_generic_ids:
        str_id = str(allowed_id)
        if str_id in user_vehicle_data:
            valid_ids_in_garage.extend(user_vehicle_data[str_id])
            
    return list(set(valid_ids_in_garage))