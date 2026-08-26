import asyncio
import json
import os
import re

from utils.pretty_print import display_info, display_error, display_warning
from utils.vehicle_manager import VehicleManager
from data.config_settings import get_server_code, get_server_url, is_alliance_mission_name

# Singleton — dynamic code, cache-aware
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
    display_info(f"Mission VehicleManager reloaded for code={VEHICLE_MANAGER.code}")
    return VEHICLE_MANAGER

NON_VEHICLE_KEYWORDS = [
    'water', 'liters', 'gallons', 'foam', 'mousse', 'eau',
    'probability', 'patient', '%', 'min', 'max'
]

async def check_and_grab_missions(browsers, num_threads):
    first_browser = browsers[0]
    try:
        page = first_browser.contexts[0].pages[0]
        base = get_server_url().rstrip("/")
        await page.goto(base, timeout=30000)
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        # Handle Lite mode + wait for missions to load
        try:
            await page.wait_for_selector('.mission_panel, .mission_panel_red, .mission_panel_yellow', timeout=5000)
        except Exception:
            pass
        
        # Capture all mission panels (red = own urgent, yellow = shared, alliance, event)
        selectors = '.mission_panel_red, .mission_panel_yellow, .mission_panel_green, .mission_panel, .mission_alliance, [id^="mission_"]'
        mission_panels = await page.query_selector_all(selectors)
        # Deduplicate by id
        seen = set()
        mission_list = []
        for panel in mission_panels:
            try:
                m_id_attr = await panel.get_attribute('id')
                if not m_id_attr:
                    continue
                # Only care about mission_* ids
                if not m_id_attr.startswith("mission_"):
                    continue
                clean_id = m_id_attr.split('_')[-1]
                if not clean_id.isdigit() or clean_id in seen:
                    continue
                seen.add(clean_id)
                m_type_id = await panel.get_attribute('mission_type_id')
                mission_list.append({'id': clean_id, 'type': m_type_id})
            except (AttributeError, ValueError) as e:
                display_error(f"Panel parse error: {e}")
                continue
        # Fallback: also check for alliance panels specifically if none found
        if not mission_list:
            try:
                fallback = await page.query_selector_all('[id^="mission_"]')
                for panel in fallback:
                    m_id_attr = await panel.get_attribute('id')
                    if m_id_attr and m_id_attr.startswith("mission_"):
                        clean_id = m_id_attr.split('_')[-1]
                        if clean_id.isdigit() and clean_id not in seen:
                            seen.add(clean_id)
                            m_type_id = await panel.get_attribute('mission_type_id')
                            mission_list.append({'id': clean_id, 'type': m_type_id})
            except Exception:
                pass

        display_info(f"Found {len(mission_list)} missions.")
        mission_data = await split_mission_ids_among_threads(mission_list, browsers, num_threads)
        
        os.makedirs('data', exist_ok=True)
        tmp_path = 'data/mission_data.json.tmp'
        final_path = 'data/mission_data.json'
        with open(tmp_path, 'w') as outfile:
            json.dump(mission_data, outfile, indent=4)
        os.replace(tmp_path, final_path)
        display_info("Mission data stored.")
        
    except Exception as e:
        display_error(f"Error gathering mission data: {e}")

async def split_mission_ids_among_threads(mission_list, browsers, num_threads):
    mission_data = {}
    thread_missions = [mission_list[i::num_threads] for i in range(num_threads)]
    tasks = [gather_mission_info(thread_missions[i], browsers[i], i+1) for i in range(num_threads)]
    results = await asyncio.gather(*tasks)
    for result in results:
        for mission_id, data in result.items():
            mission_data[mission_id] = data
    return mission_data

async def get_on_scene_vehicles(page):
    on_scene_counts = {}
    selectors = [
        '#mission_vehicle_at_mission tr td a[vehicle_type_id]',
        '#mission_vehicle_driving tr td a[vehicle_type_id]',
        '#mission_vehicle_staging tr td a[vehicle_type_id]',
        '#mission_vehicle_on_the_way tr td a[vehicle_type_id]'
    ]
    for selector in selectors:
        try:
            vehicle_elements = await page.query_selector_all(selector)
            for el in vehicle_elements:
                try:
                    type_id_str = await el.get_attribute('vehicle_type_id')
                    if type_id_str:
                        try:
                            v_type_id = int(type_id_str)
                            on_scene_counts[v_type_id] = on_scene_counts.get(v_type_id, 0) + 1
                        except ValueError: continue
                except (AttributeError, ValueError) as e:
                    display_error(f"On-scene parse error: {e}")
                    continue
        except Exception as e:
            display_error(f"Selector {selector} failed: {e}")
    return on_scene_counts

async def gather_mission_info(mission_entries, browser, thread_id):
    mission_data = {}
    page = browser.contexts[0].pages[0]

    for index, mission_entry in enumerate(mission_entries):
        mission_id = mission_entry['id']
        
        try:
            display_info(f"Thread {thread_id}: Processing mission {index+1}/{len(mission_entries)} (ID: {mission_id})")
            base = get_server_url().rstrip("/")
            await page.goto(f"{base}/missions/{mission_id}", timeout=30000)
            # Be nice to server
            await asyncio.sleep(0.3)
            
            try:
                await page.wait_for_selector('#missionH1', timeout=5000)
                mission_name_element = await page.query_selector('#missionH1')
                mission_name = (await mission_name_element.inner_text()).strip() if mission_name_element else "Unknown"
            except Exception as e:
                display_error(f"Mission {mission_id} H1 timeout: {e}")
                continue
            
            vehicles = []
            crashed_cars = 0
            water_needed = 0
            foam_needed = 0
            current_patient_count = len(await page.query_selector_all('div.mission_patient'))
            credits_value = 0
            found_missing_info = False

            # --- 1. INTELLIGENT ALERT SCANNING ---
            try:
                alerts = await page.query_selector_all('div.alert.alert-danger, div.alert.alert-info')
                for alert in alerts:
                    text = (await alert.inner_text()).strip()
                    
                    # DATABASE LOOKUP: "What does this alert mean?"
                    matched_ids = VEHICLE_MANAGER.get_valid_ids(text)
                    is_transport_alert = False
                    
                    # Check Capabilities of matched IDs
                    for vid in matched_ids:
                        caps = VEHICLE_MANAGER.vehicle_capabilities.get(vid, set())
                        if "PRISONER" in caps:
                            await handle_prisoner_transport(page)
                            is_transport_alert = True
                            break
                        if "PATIENT" in caps or "AMBULANCE" in caps:
                            # Transport is needed, but usually handled by 'Radio Transport' logic in main loop
                            # We mark it to ensure we don't try to dispatch an ambulance based on this alert
                            is_transport_alert = True
                            break

                    if is_transport_alert:
                        continue

                    # Resource Parsing (Legacy Support) — i18n: water/eau/wasser, foam/mousse/schaum
                    text_lower = text.lower()
                    water_words = ["water", "eau", "wasser", "liters", "gallons", "gal", "l "]
                    foam_words = ["foam", "mousse", "schaum", "schuim", "ecume"]
                    if any(w in text_lower for w in water_words) and any(x in text_lower for x in ["missing", "needed", "benötigt", "manque", "benodigd", "fehl"]):
                        match = re.search(r'([\d.,]+)\s*(?:l|liters|gal|gallons|water|eau|wasser)', text_lower)
                        if match:
                            try:
                                # Handle 1.500 vs 1,500 vs 1.5
                                num = match.group(1).replace(',', '').replace('.', '')
                                # But keep decimal if needed? For water we want int
                                water_needed = int(re.sub(r'[.,]', '', match.group(1))[:10].replace(',', '').replace('.', '') or 0)
                                # Simpler fallback
                                water_needed = int(match.group(1).replace(',', '').replace('.', '').replace(' ', '') or 0)
                            except ValueError:
                                try:
                                    water_needed = int(float(match.group(1).replace(',', '')))
                                except ValueError:
                                    pass
                    if any(w in text_lower for w in foam_words) and any(x in text_lower for x in ["missing", "needed", "benötigt", "manque", "benodigd", "fehl"]):
                        match = re.search(r'([\d.,]+)\s*(?:l|liters|gal|gallons|foam|mousse|schaum)', text_lower)
                        if match:
                            try:
                                foam_needed = int(match.group(1).replace(',', '').replace('.', '') or 0)
                            except ValueError:
                                try:
                                    foam_needed = int(float(match.group(1).replace(',', '')))
                                except ValueError:
                                    pass

            except Exception as e:
                display_error(f"Alert scan error {mission_id}: {e}")

            # --- 2. MISSING VEHICLES (Red Text - Dispatch Trigger) ---
            try:
                missing_vehicles_div = await page.query_selector('div[data-requirement-type="vehicles"]')
                if missing_vehicles_div:
                    text = (await missing_vehicles_div.inner_text()).strip().lower()
                    # i18n: handle "Missing vehicles:", "Fehlende Fahrzeuge:", "Véhicules manquants:" etc — strip up to colon
                    if ":" in text:
                        text = text.split(":", 1)[-1]
                    text = text.replace('\xa0', ' ').strip()
                    
                    vehicle_entries = text.split(',')
                    for entry in vehicle_entries:
                        try:
                            match = re.search(r'(\d+)\s+(.+)', entry.strip())
                            if match:
                                count = int(match.group(1))
                                name = match.group(2).strip().lower()
                                if name.endswith('s') and not name.endswith('ems') and not name.endswith('ss'): name = name[:-1]
                                
                            # Filter Resources — i18n
                            if any(w in name for w in ["water", "wasser", "eau", "liters", "gallons"]) and not any(x in name for x in ["tanker", "rescue", "trailer", "boat", "wassertank", "tank"]):
                                water_needed = max(water_needed, count)
                                continue
                            if any(w in name for w in ["foam", "mousse", "schaum", "schuim"]) and not any(x in name for x in ["tender", "trailer", "anhaenger", "anhänger"]):
                                foam_needed = max(foam_needed, count)
                                continue

                                if name == "car to tow":
                                    crashed_cars = count
                                else:
                                    vehicles.append({"name": name, "count": count})
                        except (ValueError, AttributeError) as e:
                            display_error(f"Vehicle entry parse error: {e}")
                            continue
                    found_missing_info = True
            except Exception as e:
                display_error(f"Missing vehicles parse error {mission_id}: {e}")

            if found_missing_info or crashed_cars > 0:
                if current_patient_count > 0:
                    vehicles.append({"name": "ambulance", "count": current_patient_count})
                
                mission_data[mission_id] = {
                    "mission_name": f"Missing: {mission_name}",
                    "credits": 0,
                    "vehicles": vehicles,
                    "patients": current_patient_count,
                    "crashed_cars": crashed_cars,
                    "water_needed": water_needed,
                    "foam_needed": foam_needed,
                    "required_personnel": []
                }
                continue

            # --- 3. STANDARD REQUIREMENTS ---
            raw_requirements = []
            try:
                await page.click('#mission_help')
                await page.wait_for_selector('#iframe-inside-container', timeout=5000)
                raw_requirements, scraped_credits = await gather_vehicle_requirements(page)
                credits_value = scraped_credits
                await page.keyboard.press('Escape')
                await asyncio.sleep(0.5)
            except Exception as e:
                display_error(f"Help iframe error {mission_id}: {e}")

            # --- 4. CALCULATE REMAINING NEEDS ---
            vehicles_on_scene = await get_on_scene_vehicles(page)
            final_vehicles_needed = []
            
            for req in raw_requirements:
                req_name = req["name"]
                req_count = req["count"]
                if "ambulance" in req_name.lower(): continue

                required_generic_ids = VEHICLE_MANAGER.get_valid_ids(req_name)
                
                count_on_scene = 0
                for type_id, scene_count in vehicles_on_scene.items():
                    if type_id in required_generic_ids:
                        count_on_scene += scene_count
                
                needed_count = max(0, req_count - count_on_scene)
                
                if needed_count > 0:
                    final_vehicles_needed.append({"name": req_name, "count": needed_count})
            
            vehicles = final_vehicles_needed
            
            if current_patient_count > 0:
                amb_generic_ids = VEHICLE_MANAGER.get_valid_ids("ambulance")
                amb_on_scene = 0
                for type_id, scene_count in vehicles_on_scene.items():
                     if type_id in amb_generic_ids:
                         amb_on_scene += scene_count
                needed_amb = max(0, current_patient_count - amb_on_scene)
                if needed_amb > 0:
                     vehicles.append({"name": "ambulance", "count": needed_amb})

            mission_data[mission_id] = {
                "mission_name": mission_name,
                "credits": credits_value,
                "vehicles": vehicles,
                "patients": current_patient_count,
                "crashed_cars": crashed_cars,
                "water_needed": water_needed,
                "foam_needed": foam_needed,
                "required_personnel": []
            }
        except asyncio.CancelledError:
            raise
        except Exception as e:
            display_error(f"Mission {mission_id} error: {e}")
            continue

    return mission_data

def remove_plural_suffix(vehicle_name):
    parts = vehicle_name.split()
    if parts and parts[-1].endswith('s') and not parts[-1].lower().endswith('ss') and not parts[-1].lower() == 'gas':
        parts[-1] = parts[-1][:-1]
    return ' '.join(parts)

async def gather_vehicle_requirements(page):
    vehicle_requirements = []
    credits = 0 
    
    # Try language-specific headers first, then fallback to generic
    # US: Vehicle and Personnel Requirements, Reward and Precondition
    # DE: Fahrzeug- und Personal-Anforderungen, Belohnung und Voraussetzung
    # FR: Véhicules et personnel requis, etc.
    th_variants = [
        "Vehicle and Personnel Requirements", "Fahrzeug", "Personnel Requirements",
        "Véhicules", "Voertuigen", "Veicoli", "Pojazdy"
    ]
    th_reward = [
        "Reward and Precondition", "Belohnung", "Récompense", "Beloning", "Credits"
    ]
    requirement_table = None
    credit_table = None
    for th in th_variants:
        try:
            sel = f'div.col-md-4 > table:has(th:has-text("{th}"))'
            requirement_table = await page.query_selector(sel)
            if requirement_table:
                break
        except Exception:
            continue
    for th in th_reward:
        try:
            sel = f'div.col-md-4 > table:has(th:has-text("{th}"))'
            credit_table = await page.query_selector(sel)
            if credit_table:
                break
        except Exception:
            continue
    # Fallback: try iframe content if not found on main page
    if not requirement_table:
        try:
            iframe = await page.query_selector('#iframe-inside-container')
            if iframe:
                # Try to get frame
                frame = await iframe.content_frame()
                if frame:
                    for th in th_variants:
                        try:
                            requirement_table = await frame.query_selector(f'table:has(th:has-text("{th}"))')
                            if requirement_table:
                                # Use frame for subsequent queries
                                page = frame
                                break
                        except Exception:
                            continue
        except Exception:
            pass

    if credit_table:
        rows = await credit_table.query_selector_all('tbody tr')
        for row in rows:
            text = (await row.inner_text()).lower()
            if "average credits" in text or "credits" in text:
                match = re.search(r'([\d,]+)', text)
                if match: credits = int(match.group(1).replace(',', ''))

    if not requirement_table:
        requirement_table = await page.query_selector('#lightbox_box table')

    if requirement_table:
        rows = await requirement_table.query_selector_all('tbody tr')
        for row in rows:
            cols = await row.query_selector_all('td')
            if len(cols) < 2: continue
            
            raw_name = (await cols[0].inner_text()).strip()
            count_text = (await cols[1].inner_text()).strip()
            
            lower_name = raw_name.lower()
            if "probability" in lower_name or "%" in lower_name or "patient" in lower_name: continue
            
            if any(k in lower_name for k in NON_VEHICLE_KEYWORDS):
                if not any(valid in lower_name for valid in ["tanker", "tender", "vehicle", "rescue", "trailer", "boat"]):
                    continue

            vehicle_name = raw_name.replace("Required", "").strip()
            vehicle_name = remove_plural_suffix(vehicle_name)
            
            match = re.search(r'([\d,]+)', count_text)
            if not match: continue
            vehicle_count = int(match.group(1).replace(',', ''))
            
            vehicle_requirements.append({"name": vehicle_name, "count": vehicle_count})

    return vehicle_requirements, credits

async def handle_prisoner_transport(page):
    try:
        closest_btn = await page.query_selector('a.btn-success, a.btn-warning')
        if closest_btn:
            txt = await closest_btn.inner_text()
            # Ensure it's not a dispatch button
            if "Dispatch" not in txt and "Alarm" not in txt:
                await closest_btn.click()
                await page.wait_for_load_state('networkidle')
                return True
        return False
    except Exception as e:
        display_error(f"Prisoner transport error: {e}")
        return False