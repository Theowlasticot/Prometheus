import asyncio
import json
import os
import re
from pathlib import Path

from utils.pretty_print import display_info, display_error, display_warning
from utils.vehicle_manager import get_manager_for_code
from data.config_settings import get_server_url
from utils.humanize import human_sleep, random_mouse_jitter
import random

PROJECT_ROOT = Path(__file__).resolve().parent.parent

MISSION_META_PATH = PROJECT_ROOT / 'data' / 'mission_meta.json'

def load_mission_meta():
    try:
        if MISSION_META_PATH.exists():
            data = json.loads(MISSION_META_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def update_mission_meta(mission_ids):
    """Record first_seen timestamps (never overwrite existing)."""
    import time as _time
    now = _time.time()
    meta = load_mission_meta()
    for mid in mission_ids:
        mid = str(mid)
        if mid not in meta:
            meta[mid] = {"first_seen": now}
    try:
        MISSION_META_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(MISSION_META_PATH) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        MISSION_META_PATH.parent.joinpath(tmp).replace(MISSION_META_PATH)
    except Exception:
        pass
    return meta

def get_mission_age(mission_id):
    """Seconds since the mission was first seen (None if unknown)."""
    import time as _time
    meta = load_mission_meta()
    entry = meta.get(str(mission_id))
    if not entry or "first_seen" not in entry:
        return None
    return max(0.0, _time.time() - float(entry["first_seen"]))

# Singleton — dynamic code, cache-aware (DRY via vehicle_manager helper)
def _create_manager():
    return get_manager_for_code()

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

# Radio messages that signal a new need (escalation / reinforcements) after dispatch
RADIO_ESCALATION_KEYWORDS = [
    "we need", "needs", "needed", "benötigt", "manque", "fehlt",
    "renfort", "missing", "verstärkung", "requested", "demandé",
]

async def check_radio_escalation(page):
    """Return True if the radio log contains escalation/needs messages."""
    try:
        items = await page.query_selector_all('#radio_messages_important li, #radio_messages li')
        for it in items:
            txt = (await it.inner_text()).strip().lower()
            if any(k in txt for k in RADIO_ESCALATION_KEYWORDS):
                return True
    except Exception:
        pass
    return False

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
        green_skipped = 0
        active_ids = []  # every visible mission (red + yellow + green) — for lock cleanup
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
                active_ids.append(clean_id)
                # Skip fully-satisfied missions (green panel = nothing missing, no escalation)
                # Escalations turn them red again -> next loop catches them.
                inner_green = await panel.query_selector('.mission_panel_green')
                inner_red = await panel.query_selector('.mission_panel_red')
                inner_yellow = await panel.query_selector('.mission_panel_yellow')
                if inner_green and not inner_red and not inner_yellow:
                    green_skipped += 1
                    continue
                m_type_id = await panel.get_attribute('mission_type_id')
                mission_list.append({'id': clean_id, 'type': m_type_id})
            except (AttributeError, ValueError) as e:
                display_error(f"Panel parse error: {e}")
                continue
        if green_skipped:
            display_info(f"Skipped {green_skipped} fully-satisfied (green) missions.")
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
        
        os.makedirs(PROJECT_ROOT / 'data', exist_ok=True)
        tmp_path = PROJECT_ROOT / 'data' / 'mission_data.json.tmp'
        final_path = PROJECT_ROOT / 'data' / 'mission_data.json'
        with open(tmp_path, 'w') as outfile:
            json.dump(mission_data, outfile, indent=4)
        os.replace(tmp_path, final_path)
        # Active mission ids (including green) — used by the dispatcher's lock
        # cleanup so vehicles locked on a green mission are not freed while the
        # mission is still on the board (would cause re-dispatch on escalation).
        active_tmp = PROJECT_ROOT / 'data' / 'active_mission_ids.json.tmp'
        active_final = PROJECT_ROOT / 'data' / 'active_mission_ids.json'
        with open(active_tmp, 'w') as outfile:
            json.dump(active_ids, outfile)
        os.replace(active_tmp, active_final)
        # First-seen timestamps (alliance grace period)
        update_mission_meta(active_ids)
        display_info(f"Mission data stored ({len(active_ids)} active ids).")
        
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

async def get_on_scene_vehicles(page, wait_tables=True, wait_timeout=4000):
    on_scene_counts = {}
    if wait_tables:
        # Race guard: mission vehicle tables are rendered via AJAX — wait for at least
        # one table before counting, otherwise we would count 0 and over-dispatch.
        try:
            await page.wait_for_selector(
                '#mission_vehicle_at_mission, #mission_vehicle_driving, '
                '#mission_vehicle_staging, #mission_vehicle_on_the_way',
                timeout=wait_timeout
            )
        except Exception:
            pass
        await page.wait_for_timeout(500)  # let remaining AJAX rows fill
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
            # Be nice to server + humanize
            await human_sleep(0.32, 0.55)
            if random.random() < 0.12:
                await random_mouse_jitter(page, moves=1)
                await human_sleep(0.18, 0.5)
            
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
                    
                    # DATABASE LOOKUP: "What does this alert mean?" — guard with keywords to avoid false positives
                    lower_text = text.lower()
                    has_prisoner_kw = any(k in lower_text for k in ["prisoner", "prisonnier", "gefangene", "arrest", "cell", "jail", "transport prisoner"])
                    has_patient_kw = any(k in lower_text for k in ["patient", "transport", "ambulance", "hospital", "verwundet", "patient"])
                    matched_ids = VEHICLE_MANAGER.get_valid_ids(text) if (has_prisoner_kw or has_patient_kw) else []
                    is_transport_alert = False
                    
                    # Transport needs are handled by the dedicated transport loop
                    # (radio -> vehicle page -> hospital/cell). Here we only mark
                    # them so the scanner does not try to dispatch vehicles for it.
                    for vid in matched_ids:
                        caps = VEHICLE_MANAGER.vehicle_capabilities.get(vid, set())
                        if has_prisoner_kw and "PRISONER" in caps:
                            is_transport_alert = True
                            break
                        if has_patient_kw and ("PATIENT" in caps or "AMBULANCE" in caps):
                            is_transport_alert = True
                            break

                    if is_transport_alert:
                        continue

                    # Resource Parsing (Legacy Support) — i18n: water/eau/wasser, foam/mousse/schaum
                    text_lower = text.lower()
                    water_words = ["water", "eau", "wasser", "liters", "gallons", "gal"]
                    foam_words = ["foam", "mousse", "schaum", "schuim", "ecume"]
                    if any(w in text_lower for w in water_words) and any(x in text_lower for x in ["missing", "needed", "benötigt", "manque", "benodigd", "fehl"]):
                        match = re.search(r'([\d.,]+)\s*(?:l|liters|gal|gallons|water|eau|wasser)', text_lower)
                        if match:
                            raw = match.group(1).strip()
                            # Handle both 1,200 and 1.200 and 5,3
                            cleaned = raw.replace(' ', '')
                            # If contains both . and , assume , is thousand and . is decimal -> remove ,
                            # If only , assume decimal comma for EU (5,3 -> 5.3)
                            if ',' in cleaned and '.' in cleaned:
                                cleaned = cleaned.replace(',', '')
                            elif ',' in cleaned and '.' not in cleaned:
                                # 5,3 -> 5.3, but 1,200 -> 1200 (heuristic: if comma + 3 digits at end -> thousand)
                                if re.search(r',\d{3}$', cleaned):
                                    cleaned = cleaned.replace(',', '')
                                else:
                                    cleaned = cleaned.replace(',', '.')
                            try:
                                water_needed = int(float(cleaned))
                            except ValueError:
                                try:
                                    water_needed = int(re.sub(r'[^\d]', '', cleaned) or 0)
                                except ValueError:
                                    pass
                    if any(w in text_lower for w in foam_words) and any(x in text_lower for x in ["missing", "needed", "benötigt", "manque", "benodigd", "fehl"]):
                        match = re.search(r'([\d.,]+)\s*(?:l|liters|gal|gallons|foam|mousse|schaum)', text_lower)
                        if match:
                            raw = match.group(1).strip().replace(' ', '')
                            if ',' in raw and '.' in raw:
                                raw = raw.replace(',', '')
                            elif ',' in raw and '.' not in raw:
                                if re.search(r',\d{3}$', raw):
                                    raw = raw.replace(',', '')
                                else:
                                    raw = raw.replace(',', '.')
                            try:
                                foam_needed = int(float(raw))
                            except ValueError:
                                try:
                                    foam_needed = int(re.sub(r'[^\d]', '', raw) or 0)
                                except ValueError:
                                    pass

            except Exception as e:
                display_error(f"Alert scan error {mission_id}: {e}")

            # --- 2. MISSING VEHICLES (Red Text - Dispatch Trigger) ---
            try:
                missing_vehicles_div = await page.query_selector('div[data-requirement-type="vehicles"]')
                if missing_vehicles_div:
                    text = (await missing_vehicles_div.inner_text()).strip()
                    vehicles, water_from_red, foam_from_red, crashed_from_red = parse_missing_vehicles(text)
                    water_needed = max(water_needed, water_from_red)
                    foam_needed = max(foam_needed, foam_from_red)
                    crashed_cars = max(crashed_cars, crashed_from_red)
                    found_missing_info = True
            except Exception as e:
                display_error(f"Missing vehicles parse error {mission_id}: {e}")

            if found_missing_info or crashed_cars > 0:
                # Fix double-count: only add ambulance if not already in vehicles
                if current_patient_count > 0:
                    has_amb = any("ambulance" in v.get("name","").lower() for v in vehicles)
                    if not has_amb:
                        vehicles.append({"name": "ambulance", "count": current_patient_count})
                    else:
                        # Update existing ambulance count to max
                        for v in vehicles:
                            if "ambulance" in v.get("name","").lower():
                                v["count"] = max(v["count"], current_patient_count)
                # Try to still get credits/personnel/expansions via help if missing (helps sorting & gating)
                required_expansions = []
                required_personnel = []
                try:
                    help_btn = await page.query_selector('#mission_help')
                    if help_btn and await help_btn.is_visible():
                        await help_btn.click(timeout=4000)
                        await page.wait_for_selector('#iframe-inside-container', timeout=3000)
                        _, scraped_credits, water_from_help, foam_from_help, expansions_from_help, personnel_from_help = await gather_vehicle_requirements(page)
                        if credits_value == 0:
                            credits_value = scraped_credits
                        water_needed = max(water_needed, water_from_help)
                        foam_needed = max(foam_needed, foam_from_help)
                        required_expansions = expansions_from_help
                        required_personnel = personnel_from_help
                        await page.keyboard.press('Escape')
                        await asyncio.sleep(0.3)
                except Exception:
                    try:
                        await page.keyboard.press('Escape')
                    except Exception:
                        pass
                
                mission_data[mission_id] = {
                    "mission_name": f"Missing: {mission_name}",
                    "credits": credits_value,
                    "vehicles": vehicles,
                    "patients": current_patient_count,
                    "crashed_cars": crashed_cars,
                    "water_needed": water_needed,
                    "foam_needed": foam_needed,
                    "required_personnel": required_personnel,
                    "required_expansions": required_expansions,
                    "required_total": {}
                }
                continue

            # --- 3. STANDARD REQUIREMENTS ---
            raw_requirements = []
            required_expansions = []
            required_personnel = []
            try:
                help_btn = await page.query_selector('#mission_help')
                if help_btn and await help_btn.is_visible():
                    await help_btn.click(timeout=4000)
                    await page.wait_for_selector('#iframe-inside-container', timeout=5000)
                    raw_requirements, scraped_credits, water_from_help, foam_from_help, expansions_from_help, personnel_from_help = await gather_vehicle_requirements(page)
                    credits_value = scraped_credits
                    water_needed = max(water_needed, water_from_help)
                    foam_needed = max(foam_needed, foam_from_help)
                    required_expansions = expansions_from_help
                    required_personnel = personnel_from_help
                    await page.keyboard.press('Escape')
                    await asyncio.sleep(0.5)
                else:
                    display_warning(f"Help button not visible for {mission_id}, skipping requirements scrape")
            except Exception as e:
                display_warning(f"Help iframe error {mission_id}: {e}")
                try:
                    await page.keyboard.press('Escape')
                except Exception:
                    pass

            # --- 4. CALCULATE REMAINING NEEDS (unified delta helper R3) ---
            vehicles_on_scene = await get_on_scene_vehicles(page)
            pending_counts = pending_counts_for_mission(mission_id)
            static_reqs = {req["name"]: req["count"] for req in raw_requirements}
            missing = extract_missing_requirements(
                static_reqs, vehicles_on_scene, pending_counts,
                VEHICLE_MANAGER.get_valid_ids,
            )
            final_vehicles_needed = [
                {"name": req_name, "count": count}
                for req_name, count in missing.items()
            ]
            
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
                "required_personnel": required_personnel,
                "required_expansions": required_expansions,
                "required_total": {req["name"]: req["count"] for req in raw_requirements}
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

def extract_missing_requirements(static_reqs, engaged_counts, pending_counts,
                                 get_valid_ids_fn):
    """Unified delta computation (R3): what is still missing on a mission.

    R_missing = max(0, R_required - (U_on_scene + U_driving + U_local_pending))

    static_reqs: {req_name: count} — 100% base requirements
    engaged_counts: {vehicle_type_id: count} — vehicles already on the
        mission (at_mission + driving tables, ALL players)
    pending_counts: {vehicle_type_id: count} — vehicles the bot locked
        locally for this mission (in-flight dispatch latency window)
    get_valid_ids_fn: callable(req_name) -> iterable(vehicle_type_id)

    Returns {req_name: missing_count} (only needs still > 0).
    """
    missing = {}
    for req_name, count in static_reqs.items():
        if "ambulance" in str(req_name).lower():
            continue
        try:
            valid_ids = set(get_valid_ids_fn(req_name))
        except Exception:
            valid_ids = set()
        engaged = sum(c for t, c in (engaged_counts or {}).items() if t in valid_ids)
        pending = sum(c for t, c in (pending_counts or {}).items() if t in valid_ids)
        need = max(0, int(count) - engaged - pending)
        if need > 0:
            missing[req_name] = need
    return missing


def pending_counts_for_mission(mission_id):
    """{vehicle_type_id: count} of vehicles the bot sent/locked for a mission
    (persisted dispatch_state + in-flight locks) — covers the server's
    status-update latency window."""
    counts = {}
    try:
        from utils.vehicle_lock import LOCK_MANAGER
        vids = set(LOCK_MANAGER.sent_vehicles_of(str(mission_id)))
    except Exception:
        vids = set()
    if not vids:
        return counts
    try:
        vd = json.loads((PROJECT_ROOT / 'data' / 'vehicle_data.json').read_text(encoding="utf-8"))
        by_type = vd.get("by_type") or {
            k: v for k, v in vd.items() if k not in ("by_type", "crew")
        }
        vid_to_type = {}
        for tid, ids in by_type.items():
            for vid in ids:
                vid_to_type[str(vid)] = int(tid)
    except Exception:
        return counts
    for vid in vids:
        t = vid_to_type.get(str(vid))
        if t is not None:
            counts[t] = counts.get(t, 0) + 1
    return counts


def parse_missing_vehicles(text):
    """Parse the red 'Missing Vehicles' window text into (vehicles, water, foam, crashed).

    Shared between mission scraping and dispatch-time re-read so both use the
    exact same rules (fenêtre rouge = source de vérité).
    """
    vehicles = []
    water_needed = 0
    foam_needed = 0
    crashed_cars = 0
    if not text:
        return vehicles, water_needed, foam_needed, crashed_cars
    if ":" in text:
        text = text.split(":", 1)[-1]
    text = text.replace('\xa0', ' ').strip()

    for entry in text.split(','):
        match = re.search(r'(\d+)\s+(.+)', entry.strip())
        if not match:
            continue
        count = int(match.group(1))
        name = match.group(2).strip().lower()
        if name.endswith('s') and not name.endswith('ems') and not name.endswith('ss'):
            name = name[:-1]

        # Resources — i18n (water/eau/wasser, foam/mousse/schaum) — kept as quantities, not vehicles
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
    return vehicles, water_needed, foam_needed, crashed_cars

async def gather_vehicle_requirements(page):
    vehicle_requirements = []
    credits = 0
    water_needed = 0
    foam_needed = 0
    required_expansions = []
    required_personnel = []
    
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
            # Check for required expansions (e.g., "Required Forestry expansions: 3")
            if "expansions" in text or "expansion" in text:
                try:
                    cols = await row.query_selector_all('td')
                    if len(cols) >= 2:
                        raw_name = (await cols[0].inner_text()).strip()
                        # Extract expansion name: remove "Required" and "expansions"/"expansion"
                        exp_name = raw_name.replace("Required", "").replace("required", "").replace("Expansions", "").replace("expansions", "").replace("Expansion", "").replace("expansion", "").replace(":", "").strip()
                        # Handle cases like "Forestry expansions" -> "Forestry"
                        exp_name = exp_name.strip()
                        if exp_name and exp_name not in required_expansions:
                            required_expansions.append(exp_name)
                except Exception:
                    pass

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

            # Handle water/foam resource requirements (e.g., "Water required: 16000", "Foam required: 175")
            # These appear in help table as non-vehicle rows and should be captured as water_needed/foam_needed
            is_water_req = any(w in lower_name for w in ["water", "wasser", "eau", "liters", "gallons", "gal"])
            is_foam_req = any(w in lower_name for w in ["foam", "mousse", "schaum", "schuim", "ecume"])
            if (is_water_req or is_foam_req) and any(k in lower_name for k in ["required", "needed", "benötigt", "manque", "benodigd", "need"]):
                # Ensure it's not a vehicle like "Water Tanker" (which contains tanker)
                if not any(valid in lower_name for valid in ["tanker", "tender", "vehicle", "rescue", "trailer", "boat"]):
                    match = re.search(r'([\d,]+)', count_text)
                    if match:
                        try:
                            val = int(match.group(1).replace(',', '').strip())
                            if is_foam_req:
                                foam_needed = max(foam_needed, val)
                            else:
                                water_needed = max(water_needed, val)
                            continue  # don't add as vehicle
                        except ValueError:
                            pass

            # Handle personnel requirements (e.g., "Required Personnel: 8x HazMat")
            if "personnel" in lower_name or "personal" in lower_name:
                # Parse like "8x HazMat" or "4x HazMat"
                match = re.search(r'(\d+)\s*x\s*(.+)', count_text, re.IGNORECASE)
                if not match:
                    # Try alternative format: "HazMat 8"
                    match = re.search(r'(\d+)\s+(.+)', raw_name)
                    if match:
                        # This is likely not personnel but vehicle, so skip
                        pass
                    else:
                        # Try to parse count_text as "8x HazMat"
                        m2 = re.search(r'(\d+)', count_text)
                        if m2:
                            cnt = int(m2.group(1))
                            # Extract name after x
                            name_part = re.sub(r'^\d+\s*x\s*', '', count_text, flags=re.IGNORECASE).strip()
                            if name_part:
                                required_personnel.append({"name": name_part, "count": cnt})
                                continue
                else:
                    cnt = int(match.group(1))
                    name_part = match.group(2).strip()
                    # Clean up name (remove parentheses)
                    name_part = re.sub(r'\(.*\)', '', name_part).strip()
                    if name_part:
                        required_personnel.append({"name": name_part, "count": cnt})
                        continue
            
            if any(k in lower_name for k in NON_VEHICLE_KEYWORDS):
                if not any(valid in lower_name for valid in ["tanker", "tender", "vehicle", "rescue", "trailer", "boat"]):
                    continue

            vehicle_name = raw_name.replace("Required", "").strip()
            vehicle_name = remove_plural_suffix(vehicle_name)
            
            match = re.search(r'([\d,]+)', count_text)
            if not match: continue
            vehicle_count = int(match.group(1).replace(',', ''))
            
            vehicle_requirements.append({"name": vehicle_name, "count": vehicle_count})

    # Also check other tables (e.g., "Other information" for personnel like "Required Personnel: 40x Hotshot")
    # The above only covered the Vehicle table; check all tables for personnel/expansions
    try:
        all_tables = await page.query_selector_all('div.col-md-4 > table, #lightbox_box table')
        for tbl in all_tables:
            if tbl == requirement_table or tbl == credit_table:
                continue
            rows = await tbl.query_selector_all('tbody tr')
            for row in rows:
                try:
                    cols = await row.query_selector_all('td')
                    if len(cols) < 2:
                        continue
                    raw_name = (await cols[0].inner_text()).strip()
                    count_text = (await cols[1].inner_text()).strip()
                    lower_name = raw_name.lower()
                    if "personnel" in lower_name or "personal" in lower_name:
                        m = re.search(r'(\d+)\s*x\s*(.+)', count_text, re.IGNORECASE)
                        if m:
                            cnt = int(m.group(1))
                            name_part = re.sub(r'\(.*\)', '', m.group(2).strip()).strip()
                            if name_part and not any(p["name"] == name_part for p in required_personnel):
                                required_personnel.append({"name": name_part, "count": cnt})
                    # Also check for expansions in other tables (fallback)
                    if "expansions" in lower_name or "expansion" in lower_name:
                        raw_name2 = (await cols[0].inner_text()).strip()
                        exp_name = raw_name2.replace("Required", "").replace("required", "").replace("Expansions", "").replace("expansions", "").replace("Expansion", "").replace("expansion", "").replace(":", "").strip()
                        if exp_name and exp_name not in required_expansions:
                            required_expansions.append(exp_name)
                except Exception:
                    continue
    except Exception:
        pass

    return vehicle_requirements, credits, water_needed, foam_needed, required_expansions, required_personnel