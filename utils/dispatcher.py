import json
import asyncio
import os

from utils.pretty_print import display_info, display_error
from utils.vehicle_manager import VehicleManager
from data.config_settings import get_share_alliance, get_process_alliance

VEHICLE_MANAGER = VehicleManager(data_folder="us")
VEHICLE_DATA_CACHE = None
USER_TO_SYSTEM_MAP = {} 

async def load_vehicle_data(force=False):
    global VEHICLE_DATA_CACHE, USER_TO_SYSTEM_MAP
    if VEHICLE_DATA_CACHE is None or force:
        try:
            with open('data/vehicle_data.json', 'r') as file:
                VEHICLE_DATA_CACHE = json.load(file)
            
            # Build Reverse Map for Intelligent Logic
            USER_TO_SYSTEM_MAP = {}
            for sys_id, user_ids in VEHICLE_DATA_CACHE.items():
                for uid in user_ids:
                    USER_TO_SYSTEM_MAP[str(uid)] = int(sys_id)
        except:
            VEHICLE_DATA_CACHE = {}
            USER_TO_SYSTEM_MAP = {}
    return VEHICLE_DATA_CACHE

async def navigate_and_dispatch(browsers):
    try:
        with open('data/mission_data.json', 'r') as file:
            mission_data = json.load(file)
    except FileNotFoundError:
        display_error("mission_data.json not found.")
        return

    await load_vehicle_data(force=True)
    page = browsers[0].contexts[0].pages[0]

    sorted_missions = sorted(
        mission_data.items(),
        key=lambda item: (
            1 if "missing" in item[1].get("mission_name", "").lower() or "incomplete" in item[1].get("mission_name", "").lower() else 0,
            item[1].get("credits", 0)
        ),
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

        is_missing_mission = "missing" in mission_name.lower() or "incomplete" in mission_name.lower()
        is_alliance_mission = "[alliance]" in mission_name.lower()

        if is_alliance_mission and not get_process_alliance():
            display_info(f"⏭️ Skipping Alliance Mission: {mission_name}")
            continue

        display_info(f"Checking mission: {mission_name} ({credits_val} Cr) (ID: {mission_id})")

        try:
            await page.goto(f"https://www.missionchief.com/missions/{mission_id}")
            await page.wait_for_selector('#missionH1', timeout=5000)
        except:
            display_error(f"Mission {mission_id} failed to load.")
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
            except: pass

        display_info(f"✅ Dispatching: {reason}")

        try:
            load_btn = await page.query_selector('a.missing_vehicles_load.btn-warning')
            if load_btn:
                await load_btn.click()
                await page.wait_for_load_state('networkidle')
                await page.wait_for_timeout(1000)
        except: pass

        # --- SELECT VEHICLES ---
        vehicle_requirements = data.get("vehicles", [])
        available_vehicles_elements = await page.query_selector_all('input.vehicle_checkbox:visible')
        used_vehicle_ids = []
        
        current_water = 0
        current_foam = 0

        for requirement in vehicle_requirements:
            req_name = requirement["name"]
            req_count = requirement["count"]
            
            if "ambulance" in req_name.lower(): continue

            valid_ids = await get_valid_ids_for_type(req_name) 
            selected = 0
            
            for cb in available_vehicles_elements:
                v_id = await cb.get_attribute("value")
                is_checked = await cb.is_checked()
                
                if v_id in used_vehicle_ids or is_checked: 
                    if is_checked and v_id not in used_vehicle_ids: 
                        used_vehicle_ids.append(v_id)
                    continue
                
                if v_id in valid_ids:
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
                    
                    w = int(await cb.get_attribute("wasser_amount") or 0)
                    f = int(await cb.get_attribute("foam_amount") or await cb.get_attribute("foam_amount_display") or 0)
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
                
                w = int(await cb.get_attribute("wasser_amount") or 0)
                f = int(await cb.get_attribute("foam_amount") or await cb.get_attribute("foam_amount_display") or 0)
                
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

        # --- SEND ---
        btn = await page.query_selector('#alert_btn')
        if btn:
            if len(used_vehicle_ids) == 0:
                display_info(f"⛔ No vehicles selected for {mission_id}. Skipping dispatch click.")
                continue

            await btn.click()
            display_info(f"🚀 Dispatched mission {mission_id}")

async def check_mission_requirements_global_percent(page, mission_data):
    checkboxes = await page.query_selector_all('input.vehicle_checkbox:visible')
    available_ids_pool = []
    for cb in checkboxes:
        v = await cb.get_attribute("value")
        if v: available_ids_pool.append(v)
    
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

    if total_found > 0:
        return True, f"Partial Match: {total_found}/{total_needed}"
    
    return False, f"Insufficient: 0/{total_needed} vehicles found. (Empty Fleet)"

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