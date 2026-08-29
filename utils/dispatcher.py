import json
import asyncio
import re
from pathlib import Path

from utils.pretty_print import display_info, display_error, display_warning
from utils.vehicle_manager import get_manager_for_code
from data.config_settings import get_share_alliance, get_process_alliance, get_server_url, is_alliance_mission_name, get_min_percent, get_use_aar, get_ignore_storm, get_ignore_event, get_min_credits, get_two_stage, get_require_training, get_alliance_delay, get_max_dispatch_distance, get_strict_trailer_pairing, get_require_personnel_education, get_strict_crew, get_fallback_dispatch, get_radius_by_class, get_alliance_mode
from utils.humanize import human_sleep, random_mouse_jitter
from utils.mission_data import parse_missing_vehicles, get_on_scene_vehicles, get_mission_age, extract_missing_requirements, pending_counts_for_mission
from utils.building_data import load_building_data, has_expansion
import random

# Trailer types that require towing vehicle (cannot dispatch alone)
TRAILER_IDS = {7, 31, 35, 36, 37, 38, 41, 46, 59}  # water trailer, foam trailer, etc. — approximate US
# Vehicle locking to avoid double-dispatch across missions (inspired by NatesHonor)
# Two layers: TTL in-flight lock + persisted sent map (see utils/vehicle_lock.py)
from utils.vehicle_lock import LOCK_MANAGER

def is_vehicle_locked(vid: str) -> bool:
    return LOCK_MANAGER.is_locked(vid)

def lock_vehicle(vid: str, mission_id: str):
    LOCK_MANAGER.lock_batch([vid], mission_id)

def free_up_vehicles(mission_id: str):
    LOCK_MANAGER.release_mission(mission_id)

def unlock_vehicle(vid: str):
    LOCK_MANAGER.unlock_vehicle(vid)

def free_all_vehicles():
    LOCK_MANAGER.free_all()

def greedy_plan(vehicle_manager, remaining, valid_per_req, avail):
    """Pure greedy planner (testable offline).

    remaining: {req_name: count} (net needs)
    valid_per_req: {req_name: set(vid)}
    avail: list of (vid, sys_id, dist, can_satisfy) — available, unlocked vehicles
    Returns (steps, final_remaining):
      steps: [(vid, target_req, is_multi, satisfiable_reqs)]
    Rules (best-first, per selection):
      - exact type wins: a vehicle whose own role name matches a remaining
        requirement is preferred over substitutes (MCV->BCU overlap)
      - multi-role collapse only for vehicles declared in multi_role.json
      - then distance asc
    """
    steps = []
    rem = dict(remaining)
    cand = list(avail)
    while any(c > 0 for c in rem.values()):
        best = None
        for item in cand:
            vid, sys_id, dist, can_satisfy = item
            satisfiable = [r for r, c in rem.items() if c > 0 and vid in valid_per_req.get(r, set())]
            if not satisfiable:
                continue
            exact = 0
            prim = None
            try:
                prim = vehicle_manager.primary_name(sys_id)
            except Exception:
                prim = None
            if prim:
                norm = vehicle_manager.normalize(prim)
                if any(vehicle_manager.normalize(r) == norm for r in satisfiable):
                    exact = 1
            is_multi = False
            try:
                is_multi = bool(sys_id and vehicle_manager.is_true_multi_role(sys_id))
            except Exception:
                is_multi = False
            can = len(satisfiable)
            score = (exact, 1 if (is_multi and can >= 2) else 0, can, -dist)
            if best is None or score > best[0]:
                best = (score, item, satisfiable)
        if best is None:
            break
        _score, (vid, sys_id, dist, can_satisfy), satisfiable = best
        # Exact-type preference for the slot this vehicle fills
        target_req = max(satisfiable, key=lambda r: rem[r])
        prim = None
        try:
            prim = vehicle_manager.primary_name(sys_id)
        except Exception:
            prim = None
        if prim:
            norm = vehicle_manager.normalize(prim)
            for r in satisfiable:
                if vehicle_manager.normalize(r) == norm:
                    target_req = r
                    break
        is_multi = False
        try:
            is_multi = bool(sys_id and vehicle_manager.is_true_multi_role(sys_id))
        except Exception:
            is_multi = False
        if is_multi and len(satisfiable) >= 2:
            for r in satisfiable:
                if rem[r] > 0:
                    rem[r] -= 1
        else:
            rem[target_req] -= 1
        steps.append((vid, target_req, is_multi and len(satisfiable) >= 2, satisfiable))
        cand.remove((vid, sys_id, dist, can_satisfy))
    return steps, rem

def order_ambulance_ids(vehicle_manager, ambulance_ids, user_to_system_map):
    """Pure ambulances (5, 11, 20: standard/ALS/Mass Casualty) first; combined
    vehicles (48, 49, 50: EMS Fire Engine, Tactical, HazMat Ambulance) last —
    they must not be pulled off the fire scene for transport when a pure
    ambulance is free."""
    AMBULANCE_PURE_TYPES = {5, 11, 20}
    AMBULANCE_COMBI_TYPES = {48, 49, 50}
    pure = [v for v in ambulance_ids if user_to_system_map.get(str(v)) in AMBULANCE_PURE_TYPES]
    combi = [v for v in ambulance_ids if user_to_system_map.get(str(v)) in AMBULANCE_COMBI_TYPES]
    others = [v for v in ambulance_ids if v not in pure and v not in combi]
    return pure + combi + others

async def read_water_status(page):
    """Read the game's live water bars (at mission / driving / selected) -> (total, need).

    The game itself tracks water already committed (on scene + approaching +
    selected in the dispatch window). We must count from there, not from 0,
    otherwise we over-send tankers (verified live: 'Fire in a cell' had
    6,000 on scene + 16,187 approaching for a 8,000 need -> bot sent more).
    """
    total = 0
    need = 0
    try:
        for cls in ("mission_water_bar_at_mission_", "mission_water_bar_driving_", "mission_water_bar_selected_"):
            el = await page.query_selector(f'div[class*="{cls}"]')
            if el:
                v = await el.get_attribute('data-water-has')
                if v and v.isdigit():
                    total += int(v)
        need_el = await page.query_selector('div[class*="mission_water_bar_missing_"]')
        if need_el:
            v = await need_el.get_attribute('data-need_water')
            if v and v.isdigit():
                need = int(v)
    except Exception:
        pass
    return total, need

async def read_foam_status(page):
    """Same as water but for foam bars."""
    total = 0
    need = 0
    try:
        for cls in ("mission_foam_bar_at_mission_", "mission_foam_bar_driving_", "mission_foam_bar_selected_"):
            el = await page.query_selector(f'div[class*="{cls}"]')
            if el:
                v = await el.get_attribute('data-foam-has')
                if v and v.isdigit():
                    total += int(v)
        need_el = await page.query_selector('div[class*="mission_foam_bar_missing_"]')
        if need_el:
            v = await need_el.get_attribute('data-need_foam')
            if v and v.isdigit():
                need = int(v)
    except Exception:
        pass
    return total, need

async def _read_cb_amount(cb, *attr_names) -> int:
    """Read a numeric amount attribute (water/foam) from a vehicle checkbox."""
    for attr in attr_names:
        try:
            raw = await cb.get_attribute(attr)
            if raw is not None:
                return int(str(raw).replace(',', '').strip() or 0)
        except ValueError:
            continue
        except Exception:
            continue
    return 0

async def count_patients_needing_ambulance(page):
    """Count patients lacking an ambulance via live 'We need: Ambulance' alerts.

    Returns (need, total_patients). When no such alert exists, every visible
    patient already has an ambulance assigned -> 0 extra needed (hospital
    transport is handled separately by the transport logic).
    """
    need = 0
    total_patients = 0
    try:
        total_patients = len(await page.query_selector_all('div.mission_patient'))
        texts = []
        alerts = await page.query_selector_all('div.alert')
        for a in alerts:
            try:
                t = (await a.inner_text()).strip().lower()
            except Exception:
                continue
            if "ambulance" in t:
                texts.append(t)
        combined = 0
        individual = 0
        for t in texts:
            m = re.search(r'^(\d+)x\s+we need:\s*ambulance', t)
            if m:
                combined = max(combined, int(m.group(1)))
            elif "we need" in t or "brauchen" in t or "besoin" in t or "nodig" in t:
                individual += 1
        need = combined if combined > 0 else individual
    except Exception:
        pass
    return need, total_patients

async def get_vehicle_distances(page, vehicle_ids: list[str]) -> dict[str, float]:
    """Batch JS like NatesHonor vehicles.py:8 — single evaluate vs loop."""
    if not vehicle_ids:
        return {}
    try:
        script = """
        (ids) => {
            const result = {};
            for (const id of ids) {
                const el = document.querySelector(`#vehicle_sort_${id}`);
                if (el) {
                    const val = el.getAttribute('sortvalue');
                    result[id] = val ? val.replace(',', '.') : 'inf';
                } else {
                    result[id] = 'inf';
                }
            }
            return result;
        }
        """
        raw = await page.evaluate(script, vehicle_ids)
        distances = {}
        for vid in vehicle_ids:
            val = raw.get(vid, 'inf')
            if val == 'inf' or val is None:
                distances[vid] = float('inf')
            else:
                try:
                    distances[vid] = float(str(val).replace(',', '.'))
                except ValueError:
                    distances[vid] = float('inf')
        return distances
    except Exception:
        # Fallback loop (old)
        distances = {}
        for vid in vehicle_ids:
            try:
                el = await page.query_selector(f'#vehicle_sort_{vid}')
                if el:
                    val = await el.get_attribute('sortvalue')
                    if val is not None:
                        try:
                            distances[vid] = float(val.replace(',', '.'))
                            continue
                        except ValueError:
                            pass
                distances[vid] = float('inf')
            except Exception:
                distances[vid] = float('inf')
        return distances

# Dynamic manager — uses shared helper from vehicle_manager (DRY)
def _create_manager():
    return get_manager_for_code()

VEHICLE_MANAGER = _create_manager()

def reload_vehicle_manager():
    global VEHICLE_MANAGER
    VEHICLE_MANAGER = _create_manager()
    display_info(f"VehicleManager reloaded for code={VEHICLE_MANAGER.code} from {VEHICLE_MANAGER.data_folder}")
    return VEHICLE_MANAGER
VEHICLE_DATA_CACHE = None
USER_TO_SYSTEM_MAP = {}
CREW_DATA = {}  # vid -> {"personnel": N, "educations": [names]}

PROJECT_ROOT = Path(__file__).resolve().parent.parent

async def load_vehicle_data(force=False):
    global VEHICLE_DATA_CACHE, USER_TO_SYSTEM_MAP, CREW_DATA
    if VEHICLE_DATA_CACHE is None or force:
        try:
            with open(PROJECT_ROOT / 'data' / 'vehicle_data.json', 'r') as file:
                VEHICLE_DATA_CACHE = json.load(file)

            # New schema: {"by_type": {...}, "crew": {...}}
            # Legacy schema: {"5": [vids], ...} — migrate transparently
            CREW_DATA = VEHICLE_DATA_CACHE.get("crew", {}) if isinstance(VEHICLE_DATA_CACHE, dict) else {}
            by_type = VEHICLE_DATA_CACHE.get("by_type", None) if isinstance(VEHICLE_DATA_CACHE, dict) else None
            if by_type is None:
                by_type = {k: v for k, v in VEHICLE_DATA_CACHE.items() if k not in ("by_type", "crew")}
                VEHICLE_DATA_CACHE = {"by_type": by_type, "crew": CREW_DATA}

            # Build Reverse Map for Intelligent Logic
            USER_TO_SYSTEM_MAP = {}
            for sys_id, user_ids in by_type.items():
                for uid in user_ids:
                    USER_TO_SYSTEM_MAP[str(uid)] = int(sys_id)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            display_warning(f"Vehicle data load failed: {e}")
            VEHICLE_DATA_CACHE = {"by_type": {}, "crew": {}}
            USER_TO_SYSTEM_MAP = {}
            CREW_DATA = {}
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

    # --- Vehicle lock cleanup: free vehicles only for missions no longer on the board ---
    # Use active_mission_ids.json (red + yellow + green) so locks on green missions
    # survive until the mission disappears entirely — otherwise an escalation would
    # re-dispatch vehicles that are still driving.
    try:
        LOCK_MANAGER.load_state()
        LOCK_MANAGER.cleanup()
        active_ids = set(mission_data.keys())
        try:
            with open(PROJECT_ROOT / 'data' / 'active_mission_ids.json', 'r') as f:
                board_ids = set(json.load(f))
            if board_ids:
                active_ids = board_ids
        except Exception:
            pass
        locked_mids = LOCK_MANAGER.sent_missions()
        stale = locked_mids - active_ids
        for mid in stale:
            free_up_vehicles(mid)
            display_info(f"Freed vehicles for completed mission {mid}")
        if len(LOCK_MANAGER) > 400:
            display_warning(f"Lock table large ({len(LOCK_MANAGER)}), clearing stale locks")
            free_all_vehicles()
        if locked_mids:
            display_info(f"Lock table: {len(LOCK_MANAGER)} vehicles locked across {len(locked_mids)} missions")
    except Exception as e:
        display_warning(f"Lock cleanup error: {e}")

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

        # Alliance grace period: let allies send their units first so the
        # live red window / on-scene tables include their vehicles before we
        # compute the differential (avoids doubling alliance colleagues).
        if is_alliance_mission:
            try:
                alliance_delay = get_alliance_delay()
            except Exception:
                alliance_delay = 45
            if alliance_delay > 0:
                age = get_mission_age(mission_id)
                if age is None or age < alliance_delay:
                    display_info(f"⏳ Alliance {mission_id} age {age if age is not None else '?'}s < {alliance_delay}s — waiting for allies")
                    continue

        display_info(f"Checking mission: {mission_name} ({credits_val} Cr) (ID: {mission_id})")

        try:
            base = get_server_url().rstrip("/")
            await page.goto(f"{base}/missions/{mission_id}", timeout=30000)
            await page.wait_for_selector('#missionH1', timeout=5000)
            # Human-like pause after page load + occasional scroll
            await human_sleep(0.42, 0.55)
            if random.random() < 0.18:
                await page.mouse.wheel(0, random.randint(80, 260))
                await human_sleep(0.22, 0.6)
        except Exception as e:
            display_error(f"Mission {mission_id} failed to load: {e}")
            await human_sleep(0.9, 0.4)
            continue

        # --- ALLIANCE CREDIT-ONLY MODE ---
        # Earn the alliance credit with ONE nearby unit instead of solving
        # the whole mission with local fleet (preserves fleet for personal
        # missions). The grace period above still applies.
        if is_alliance_mission:
            try:
                alliance_mode = get_alliance_mode()
            except Exception:
                alliance_mode = "full"
            if alliance_mode == "credit_only":
                sent = await _dispatch_credit_unit(page, mission_id)
                if sent:
                    display_info(f"🤝 Credit-only unit sent for alliance mission {mission_id}")
                else:
                    display_info(f"🤝 Credit-only: no eligible unit for alliance mission {mission_id}")
                continue

        # --- LIVE RED WINDOW RE-READ (fenêtre rouge = source de vérité) ---
        # The file snapshot may be stale (vehicles arrived since scrape). Re-read
        # the red window on the page right now and use it authoritatively.
        try:
            red = await page.query_selector('div[data-requirement-type="vehicles"]')
            if red:
                red_text = (await red.inner_text()).strip()
                red_vehicles, red_water, red_foam, red_crashed = parse_missing_vehicles(red_text)
                # Ambulance requests from the window feed the patients logic
                amb_count = 0
                other_reqs = []
                for r in red_vehicles:
                    if "ambulance" in r["name"].lower():
                        amb_count += r["count"]
                    else:
                        other_reqs.append(r)
                if amb_count:
                    patients_count = max(patients_count, amb_count)
                data = dict(data)
                data["vehicles"] = other_reqs
                data["water_needed"] = max(req_water, red_water)
                data["foam_needed"] = max(req_foam, red_foam)
                data["crashed_cars"] = max(crashed_cars, red_crashed)
                is_missing_mission = True
                display_info(f"🔴 Red window live for {mission_id}: {other_reqs} | water={data['water_needed']} foam={data['foam_needed']} amb={amb_count}")
        except Exception as e:
            display_warning(f"Red window re-read failed {mission_id}: {e}")

        # Re-bind locals from (possibly) re-read data so resources/selection use live values
        req_water = data.get("water_needed", 0)
        req_foam = data.get("foam_needed", 0)
        crashed_cars = data.get("crashed_cars", 0)

        # --- LIVE STANDARD PATH (fenêtre rouge absente) ---
        # Never open the help lightbox here: it destroys the vehicle checkbox
        # DOM (verified live: 126 cbs -> 0 after help open/close).
        # Instead use raw requirements stored by the scrape (same loop) and
        # re-subtract the live on-scene counts (tables are on the page, no
        # lightbox). Includes alliance missions: the tables list every player's
        # vehicles (own + allied) so we never double our allies.
        if not is_missing_mission:
            try:
                # Precise live ambulance need: only patients WITHOUT an
                # assigned ambulance (game shows 'We need: Ambulance' per
                # uncovered patient; assigned ones show missing_text null).
                amb_needed_live, live_patients = await count_patients_needing_ambulance(page)
                if live_patients > 0:
                    patients_count = amb_needed_live
                    display_info(f"🚑 Live patients for {mission_id}: {live_patients} total, {amb_needed_live} need ambulance")
                try:
                    fallback_dispatch = get_fallback_dispatch()
                except Exception:
                    fallback_dispatch = False
                if not fallback_dispatch:
                    # G2 — trust the game's red window as the only missing-vehicles
                    # signal. When it is absent the mission needs no more units,
                    # and subtracting the full template from on-scene counts would
                    # re-introduce CHANCE requirements that never triggered
                    # (e.g. a 20% HazMat the game did not roll) -> over-dispatch.
                    # Only ambulance transport needs are handled live here.
                    data = dict(data)
                    data["vehicles"] = []
                    data["water_needed"] = 0
                    data["foam_needed"] = 0
                    data["crashed_cars"] = 0
                    req_water = 0
                    req_foam = 0
                    crashed_cars = 0
                    if patients_count == 0:
                        display_info(f"✅ {mission_id} game shows no missing vehicles — skipping (fallback_dispatch=off)")
                        continue
                    display_info(f"📐 {mission_id} no missing vehicles per game — ambulance need {patients_count}")
                else:
                    req_total = data.get("required_total") or {}
                    if req_total:
                        # Wait briefly for the vehicle tables (AJAX). Fresh missions
                        # have NO tables at all (they only render once a vehicle is
                        # assigned) — that is normal, not an error.
                        on_scene = await get_on_scene_vehicles(page, wait_tables=True, wait_timeout=2500)
                        tables_present = await page.query_selector(
                            '#mission_vehicle_at_mission, #mission_vehicle_driving, '
                            '#mission_vehicle_staging, #mission_vehicle_on_the_way'
                        )
                        final_needed = []
                        if tables_present:
                            # Unified delta (R3): R_missing = required - (on scene +
                            # driving) - (locally locked/sent vehicles)
                            pending_counts = pending_counts_for_mission(mission_id)
                            missing = extract_missing_requirements(
                                req_total, on_scene, pending_counts,
                                VEHICLE_MANAGER.get_valid_ids,
                            )
                            final_needed = [
                                {"name": req_name, "count": count}
                                for req_name, count in missing.items()
                            ]
                        else:
                            # Fresh mission — fall back to the scrape-time needed list
                            # (scrape had its own AJAX waits; red window re-checked above).
                            final_needed = [r for r in data.get("vehicles", []) if "ambulance" not in r.get("name", "").lower()]
                        data = dict(data)
                        data["vehicles"] = final_needed
                        display_info(f"📐 Live standard for {mission_id}: needed {final_needed} | water={req_water} foam={req_foam} | patients={patients_count} (on-scene {len(on_scene)})")
                        if not final_needed and req_water == 0 and req_foam == 0 and patients_count == 0 and crashed_cars == 0:
                            display_info(f"✅ {mission_id} fully satisfied live — skipping")
                            continue
            except Exception as e:
                display_warning(f"Live standard failed {mission_id}: {e} — using file data")

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

        # --- TWO-STAGE GUARD: same needs already dispatched & wave still in flight ---
        # Phase 1 sends the 100% guaranteed base; Phase 2 only fires when the
        # live needs CHANGE (escalation). While the sent vehicles are still
        # driving (server not updated yet), an identical signature means the
        # current dispatch is sufficient — skip to avoid a second wave.
        try:
            if get_two_stage():
                needs_sig = _mission_needs_signature(data)
                if LOCK_MANAGER.wave_still_in_flight(mission_id, needs_sig):
                    display_info(f"🌊 {mission_id} wave in flight with identical needs — skipping re-dispatch")
                    continue
        except Exception as e:
            display_warning(f"Two-stage guard error {mission_id}: {e}")

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

        # --- LIVE RESOURCE SEED (game bars count on-scene + approaching + selected) ---
        # Read BEFORE clicking anything so the unified solver knows what is already
        # covered and does not over-send tankers/foam units.
        game_water = 0
        game_foam = 0
        try:
            game_water, game_water_need = await read_water_status(page)
            game_foam, game_foam_need = await read_foam_status(page)
            if game_water_need > 0:
                req_water = max(req_water, game_water_need)
            if game_foam_need > 0:
                req_foam = max(req_foam, game_foam_need)
            if game_water > 0 or game_foam > 0:
                display_info(f"💧 Live bars {mission_id}: water {game_water}/{req_water} foam {game_foam}/{req_foam}")
        except Exception as e:
            display_warning(f"Live water/foam read failed {mission_id}: {e}")

        # Building expansion gating (if mission_data captured it)
        required_exps = data.get("required_expansions", [])
        if required_exps:
            bdata = load_building_data()
            missing_exp = [e for e in required_exps if not has_expansion(bdata, e)]
            if missing_exp:
                display_warning(f"Skipping {mission_id} missing expansions {missing_exp}")
                continue

        # --- UNIFIED SOLVER SELECTION (roles + water + foam + personnel, one pass) ---
        # Build remaining counts per requirement (excluding ambulances)
        remaining = {}
        for req in vehicle_requirements:
            if "ambulance" in req["name"].lower():
                continue
            remaining[req["name"]] = req["count"]
        # Precompute valid_ids per requirement
        valid_per_req = {}
        for req_name in list(remaining.keys()):
            valid_per_req[req_name] = set(await get_valid_ids_for_type(req_name))
        # Build distance map for all valid ids combined
        all_valid_ids = set()
        for vids in valid_per_req.values():
            all_valid_ids.update(vids)
        try:
            dist_map = await get_vehicle_distances(page, list(all_valid_ids))
        except Exception:
            dist_map = {}
        # Cumulative personnel need (Phase 3 feeds real crew data)
        personnel_needed = 0
        try:
            req_personnel = data.get("required_personnel") or []
            personnel_needed = sum(int(p.get("count", 0)) for p in req_personnel if isinstance(p, dict))
        except Exception:
            personnel_needed = 0
        # Crew + training data (Phase 3: CREW_DATA = {vid: {personnel, educations}})
        await load_vehicle_data()
        crew_map = {}
        for vid, entry in CREW_DATA.items():
            if isinstance(entry, dict):
                crew_map[str(vid)] = int(entry.get("personnel", 0) or 0)
        require_training = False
        strict_crew = False
        trained_map = {}
        try:
            require_training = get_require_training()
        except Exception:
            require_training = False
        try:
            strict_crew = get_strict_crew()
        except Exception:
            strict_crew = False
        if require_training or strict_crew:
            for vid, entry in CREW_DATA.items():
                if not isinstance(entry, dict):
                    continue
                sys_id = USER_TO_SYSTEM_MAP.get(str(vid))
                trained_map[str(vid)] = _crew_qualified(VEHICLE_MANAGER, sys_id, entry, strict=strict_crew)

        # G1 — education-aware personnel needs (e.g. "8x HazMat" must be 8 crew
        # members holding the HazMat course, not just 8 heads).
        personnel_needs = None
        crew_educations = None
        try:
            if get_require_personnel_education():
                req_personnel = data.get("required_personnel") or []
                named = [p for p in req_personnel
                         if isinstance(p, dict) and p.get("name") and int(p.get("count", 0) or 0) > 0]
                if named:
                    personnel_needs = [
                        {"name": p["name"], "count": int(p.get("count", 0) or 0)}
                        for p in named
                    ]
                    crew_educations = {}
                    for vid, entry in CREW_DATA.items():
                        if not isinstance(entry, dict):
                            continue
                        crew_educations[str(vid)] = [
                            VEHICLE_MANAGER.normalize(e)
                            for e in entry.get("educations", []) if e
                        ]
        except Exception:
            personnel_needs = None
            crew_educations = None
        # Build candidates: available, unlocked, within dispatch radius,
        # with per-vehicle resources
        try:
            max_disp = get_max_dispatch_distance()
        except Exception:
            max_disp = 0
        # G4 — per-class radius (police:15,ambulance:15,fire:35,heavy:60,...)
        # Class entry > 0 overrides the global value for that class only.
        try:
            radius_by_class = get_radius_by_class()
        except Exception:
            radius_by_class = {}
        # Upstream trailer eligibility (strict pairing): a trailer is only a
        # solver candidate when a qualified towing vehicle exists in its own
        # station (fms 1/2, unlocked, crew trained if require_training).
        excluded_trailers = set()
        try:
            strict_pairing_pre = get_strict_trailer_pairing()
        except Exception:
            strict_pairing_pre = True
        if strict_pairing_pre:
            trailer_candidate_vids = []
            for cb in available_vehicles_elements:
                try:
                    v_id = await cb.get_attribute("value")
                    if not v_id:
                        continue
                    sys_id = USER_TO_SYSTEM_MAP.get(str(v_id))
                    if not sys_id:
                        continue
                    try:
                        is_trailer = VEHICLE_MANAGER.is_trailer(sys_id) if hasattr(VEHICLE_MANAGER, 'is_trailer') else sys_id in TRAILER_IDS
                    except Exception:
                        is_trailer = sys_id in TRAILER_IDS
                    if is_trailer:
                        trailer_candidate_vids.append((v_id, sys_id))
                except Exception:
                    continue
            if trailer_candidate_vids:
                tower_pool = await _build_tower_pool(available_vehicles_elements)
                for tv_id, tv_sys in trailer_candidate_vids:
                    try:
                        tv_cb = await page.query_selector(f'input.vehicle_checkbox[value="{tv_id}"]')
                        tv_bid = (await tv_cb.get_attribute("building_id")) or "" if tv_cb else ""
                    except Exception:
                        tv_bid = ""
                    try:
                        towing_ids = VEHICLE_MANAGER.get_towing_vehicles(tv_sys) if hasattr(VEHICLE_MANAGER, 'get_towing_vehicles') else []
                    except Exception:
                        towing_ids = []
                    eligible = trailer_local_towers(tv_bid, towing_ids, tower_pool,
                                                    require_training=require_training or strict_crew,
                                                    trained_map=trained_map)
                    if not eligible:
                        excluded_trailers.add(tv_id)
                        display_warning(f"⛔ Trailer {tv_id} (type {tv_sys}, station {tv_bid or '?'}) excluded: no qualified local towing vehicle")
        avail_sorted = []  # (cb, v_id, sys_id, dist, water, foam, crew)
        for cb in available_vehicles_elements:
            try:
                v_id = await cb.get_attribute("value")
                if not v_id:
                    continue
                if v_id in excluded_trailers:
                    continue
                sys_id_c = USER_TO_SYSTEM_MAP.get(str(v_id))
                d = dist_map.get(v_id, float('inf'))
                # Per-class radius gate (G4): class km if set (>0), else global
                try:
                    vclass = VEHICLE_MANAGER.vehicle_class(sys_id_c)
                except Exception:
                    vclass = "default"
                eff_radius = resolve_dispatch_radius(radius_by_class, vclass, max_disp)
                if not within_dispatch_radius(d, eff_radius):
                    continue
                try:
                    is_checked = await cb.is_checked()
                except Exception:
                    is_checked = False
                if is_checked or is_vehicle_locked(v_id) or v_id in used_vehicle_ids:
                    # Keep checked vehicles as already used
                    if is_checked and v_id not in used_vehicle_ids:
                        used_vehicle_ids.append(v_id)
                        lock_vehicle(v_id, mission_id)
                    continue
                # G5 — strict crew: never fail-open on specialized vehicles whose
                # crew is unknown/absent (their mission timer would stay blocked).
                if strict_crew:
                    crew_entry = CREW_DATA.get(str(v_id))
                    if not _crew_qualified(VEHICLE_MANAGER, sys_id_c, crew_entry, strict=True):
                        continue
                w = await _read_cb_amount(cb, "water_amount", "wasser_amount")
                f = await _read_cb_amount(cb, "foam_amount", "foam_amount_display")
                crew = crew_map.get(str(v_id), 0)
                avail_sorted.append((cb, v_id, sys_id_c, d, w, f, crew))
            except Exception:
                continue
        avail_plan = [
            (v_id, sys_id, d, 0, w, f, crew)
            for _cb, v_id, sys_id, d, w, f, crew in avail_sorted
        ]
        cb_by_vid = {v_id: cb for cb, v_id, _sys, _d, _w, _f, _crew in avail_sorted}

        water_need_residual = max(0, req_water - game_water)
        foam_need_residual = max(0, req_foam - game_foam)

        try:
            from utils.dispatch_solver import solve as solve_dispatch
            steps, final_remaining, totals = solve_dispatch(
                VEHICLE_MANAGER, remaining, valid_per_req, avail_plan,
                water_needed=water_need_residual,
                foam_needed=foam_need_residual,
                personnel_needed=personnel_needed,
                require_training=require_training,
                crew_trained=trained_map,
                personnel_needs=personnel_needs,
                crew_educations=crew_educations,
            )
            current_water = game_water + totals["water"]
            current_foam = game_foam + totals["foam"]
            solver_ok = True
        except Exception as e:
            display_warning(f"Unified solver failed ({e}) — falling back to greedy_plan")
            fallback_plan = [
                (v_id, sys_id, d, 0)
                for _cb, v_id, sys_id, d, _w, _f, _crew in avail_sorted
                if not (require_training or strict_crew) or trained_map.get(str(v_id), True)
            ]
            steps, final_remaining = greedy_plan(VEHICLE_MANAGER, remaining, valid_per_req, fallback_plan)
            current_water = game_water
            current_foam = game_foam
            solver_ok = False

        # Execute the plan: click each chosen vehicle
        for v_id, target_req, is_multi, satisfiable_reqs in steps:
            cb = cb_by_vid.get(v_id)
            if cb is None:
                continue
            await click_vehicle(page, cb)
            used_vehicle_ids.append(v_id)
            lock_vehicle(v_id, mission_id)
            if is_multi:
                display_info(f"Selected MULTI-ROLE {target_req} (ID: {v_id}) covers {satisfiable_reqs} [Rem: {final_remaining}]")
            else:
                display_info(f"Selected {target_req} (ID: {v_id}) [Rem: {final_remaining.get(target_req, 0) if target_req else 0}]")
        remaining = final_remaining

        # --- AMBULANCES ---
        if patients_count > 0:
            ambulance_ids = order_ambulance_ids(VEHICLE_MANAGER, await get_valid_ids_for_type("ambulance"), USER_TO_SYSTEM_MAP)
            amb_req = next((r for r in vehicle_requirements if "ambulance" in r["name"].lower()), None)
            count_to_send = amb_req["count"] if amb_req else patients_count

            # Build a map vid -> checkbox so we can iterate in the ORDERED
            # ambulance priority (pure first), not DOM order.
            cb_map = {}
            for cb in available_vehicles_elements:
                try:
                    v_id = await cb.get_attribute("value")
                    if v_id:
                        cb_map[v_id] = cb
                except Exception:
                    continue
            ambulances_sent = 0
            for v_id in ambulance_ids:
                if ambulances_sent >= count_to_send:
                    break
                if v_id in used_vehicle_ids:
                    continue
                cb = cb_map.get(v_id)
                if cb is None:
                    continue
                try:
                    if await cb.is_checked():
                        used_vehicle_ids.append(v_id)
                        continue
                except Exception:
                    pass
                await click_vehicle(page, cb)
                used_vehicle_ids.append(v_id)
                display_info(f"Selected ambulance (ID: {v_id})")
                ambulances_sent += 1

        # --- RESOURCES (fallback only: unified solver already covers water/foam) ---
        # Seed our tally from the game's live bars: the game already counts
        # on-scene + approaching + selected water/foam. Without this we would
        # re-send tankers/foam units that the game already considers covered.
        if not solver_ok:
            if req_water > current_water or req_foam > current_foam:
                potential_foam_ids = VEHICLE_MANAGER.get_ids_with_capability("FOAM")
                potential_water_ids = VEHICLE_MANAGER.get_ids_with_capability("WATER")

                # Collect resource candidates, then pick the HIGHEST capacity first:
                # e.g. 1 Foam Tender (2500f) instead of 7 Quints (25f each) for 175 foam.
                resource_candidates = []
                remaining_cbs = await page.query_selector_all('input.vehicle_checkbox:not(:checked)')
                for cb in remaining_cbs:
                    vid = await cb.get_attribute("value")
                    if vid in used_vehicle_ids:
                        continue
                    sys_id = USER_TO_SYSTEM_MAP.get(str(vid))
                    if not sys_id:
                        continue
                    needs_check = False
                    if req_foam > current_foam and sys_id in potential_foam_ids:
                        needs_check = True
                    if req_water > current_water and sys_id in potential_water_ids:
                        needs_check = True
                    if not needs_check:
                        continue
                    w = await _read_cb_amount(cb, "water_amount", "wasser_amount")
                    f = await _read_cb_amount(cb, "foam_amount", "foam_amount_display")
                    if w <= 0 and f <= 0:
                        continue
                    resource_candidates.append((cb, vid, w, f))
                # Sort by the deficient resource capacity desc
                def _res_key(item):
                    cb, vid, w, f = item
                    score = 0
                    if req_water > current_water:
                        score += w
                    if req_foam > current_foam:
                        score += f * 5  # prefer foam carriers when foam is the shortage
                    return score
                resource_candidates.sort(key=_res_key, reverse=True)

                for cb, vid, w, f in resource_candidates:
                    if current_water >= req_water and current_foam >= req_foam:
                        break
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
                        lock_vehicle(vid, mission_id)
                        display_info(f"Resource Vehicle ({vid}): +{w}W / +{f}F")

        # --- AUTOMATION SYNERGIES (EMS Chief, Sheriff, Fly-Car, Manpower) ---
        try:
            # EMS Chief auto-hospital: if EMS Chief (29) dispatched, mark patients as auto-transported
            has_ems_chief = any(USER_TO_SYSTEM_MAP.get(str(vid)) == 29 for vid in used_vehicle_ids)
            if has_ems_chief and patients_count > 0:
                display_info(f"EMS Chief auto-hospital synergy active for {patients_count} patients (no extra ambulances needed for hospital routing)")
                # Sheriff auto-prisoner: if Sheriff (47) dispatched, prisoners auto
                # (handled in transport, just log)
            has_sheriff = any(USER_TO_SYSTEM_MAP.get(str(vid)) == 47 for vid in used_vehicle_ids)
            if has_sheriff:
                display_info("Sheriff auto-prisoner synergy active")
            # Fly-Car ALS upgrade: if Fly-Car (15) present, BLS becomes ALS
            has_flycar = any(USER_TO_SYSTEM_MAP.get(str(vid)) == 15 for vid in used_vehicle_ids)
            if has_flycar:
                display_info("Fly-Car ALS upgrade active — BLS ambulances upgraded to ALS")
            # Manpower speed multiplier: log personnel count
            # Use Crew Carrier 12 crew for large fires — suggest if many personnel needed
            total_personnel = len(used_vehicle_ids) * 3  # heuristic avg 3 per vehicle
            if total_personnel < 10 and any("fire" in r.get("name","").lower() for r in vehicle_requirements):
                display_info(f"Manpower hint: {total_personnel} personnel on scene — consider Crew Carrier (12 crew) for faster completion (6 vs 3 is 2x speed)")
        except Exception as e:
            display_warning(f"Automation synergy check error: {e}")

        # --- TRAILER TOWING CHECK (using trailers.json via VehicleManager) ---
        try:
            trailer_used = []
            for vid in list(used_vehicle_ids):
                sys_id = USER_TO_SYSTEM_MAP.get(str(vid))
                # Use new VehicleManager is_trailer if available, else fallback to hardcoded
                is_trailer = False
                try:
                    if hasattr(VEHICLE_MANAGER, 'is_trailer'):
                        is_trailer = VEHICLE_MANAGER.is_trailer(sys_id) if sys_id else False
                    else:
                        is_trailer = sys_id in TRAILER_IDS
                except Exception:
                    is_trailer = sys_id in TRAILER_IDS
                if is_trailer:
                    trailer_used.append((vid, sys_id))
            if trailer_used:
                try:
                    strict_pairing = get_strict_trailer_pairing()
                except Exception:
                    strict_pairing = True
                # For each trailer, find specific towing vehicle from trailers.json
                tow_found = 0
                for trailer_vid, trailer_sys in trailer_used:
                    towing_ids = []
                    try:
                        if hasattr(VEHICLE_MANAGER, 'get_towing_vehicles'):
                            towing_ids = VEHICLE_MANAGER.get_towing_vehicles(trailer_sys)
                    except Exception:
                        towing_ids = []
                    trailer_bid = ""
                    try:
                        trailer_cb = await page.query_selector(f'input.vehicle_checkbox[value="{trailer_vid}"]')
                        if trailer_cb:
                            trailer_bid = (await trailer_cb.get_attribute("building_id")) or ""
                    except Exception:
                        trailer_bid = ""
                    # Find a towing vehicle for this trailer
                    found_for_this = False
                    for cb in available_vehicles_elements:
                        vid = await cb.get_attribute("value")
                        if vid in used_vehicle_ids or await cb.is_checked():
                            continue
                        sys_id = USER_TO_SYSTEM_MAP.get(str(vid))
                        if not sys_id:
                            continue
                        # Strict pairing: tractor must share a station building id
                        if strict_pairing and trailer_bid:
                            try:
                                tower_bid = (await cb.get_attribute("building_id")) or ""
                            except Exception:
                                tower_bid = ""
                            if not _same_station(trailer_bid, tower_bid):
                                continue
                        # Tractor must be available (fms 1/2)
                        try:
                            tower_fms = (await cb.get_attribute("fms")) or ""
                        except Exception:
                            tower_fms = ""
                        if str(tower_fms).strip() and str(tower_fms).strip() not in ("1", "2"):
                            continue
                        # Check if sys_id is in towing_ids (if defined) or is not a trailer
                        is_tower = False
                        if towing_ids:
                            is_tower = sys_id in towing_ids
                        else:
                            # Fallback: any non-trailer
                            try:
                                is_tower = not VEHICLE_MANAGER.is_trailer(sys_id) if hasattr(VEHICLE_MANAGER, 'is_trailer') else sys_id not in TRAILER_IDS
                            except Exception:
                                is_tower = sys_id not in TRAILER_IDS
                        if is_tower:
                            # Training gate: tractor crew must hold the required course
                            # (e.g. Truck Driver's License) when require_training is on.
                            if (require_training or strict_crew) and not trained_map.get(str(vid), True):
                                try:
                                    req_train = VEHICLE_MANAGER.get_required_training(sys_id) if hasattr(VEHICLE_MANAGER, 'get_required_training') else []
                                except Exception:
                                    req_train = []
                                display_warning(f"Towing vehicle {vid} skipped: crew not trained ({req_train})")
                                continue
                            try:
                                req_train = VEHICLE_MANAGER.get_required_training(sys_id) if hasattr(VEHICLE_MANAGER, 'get_required_training') else []
                                if req_train and not require_training:
                                    display_warning(f"Towing vehicle {vid} requires training {req_train} — ensure crew trained")
                            except Exception:
                                pass
                            await click_vehicle(page, cb)
                            used_vehicle_ids.append(vid)
                            lock_vehicle(vid, mission_id)
                            tow_found += 1
                            found_for_this = True
                            display_info(f"Towing vehicle {vid} for trailer {trailer_vid} (trailer type {trailer_sys}, station {trailer_bid or '?'})")
                            break
                    if not found_for_this:
                        # Atomic trailer rule: never dispatch a trailer without its tower.
                        # Uncheck the trailer so the server does not reject the dispatch.
                        if strict_pairing and trailer_bid:
                            display_warning(f"No local towing vehicle for trailer {trailer_vid} (type {trailer_sys}, station {trailer_bid}) — unchecking it")
                        else:
                            display_warning(f"No towing vehicle for trailer {trailer_vid} (type {trailer_sys}) — unchecking it")
                        try:
                            trailer_cb = await page.query_selector(f'input.vehicle_checkbox[value="{trailer_vid}"]')
                            if trailer_cb:
                                await click_vehicle(page, trailer_cb)  # toggle off
                                if trailer_vid in used_vehicle_ids:
                                    used_vehicle_ids.remove(trailer_vid)
                                unlock_vehicle(trailer_vid)
                        except Exception as e:
                            display_warning(f"Trailer uncheck failed {trailer_vid}: {e}")
                if tow_found < len(trailer_used):
                    display_warning(f"Trailer(s) {trailer_used} may lack towing vehicle ({tow_found}/{len(trailer_used)})")
        except Exception as e:
            display_warning(f"Trailer check error: {e}")

        # --- SELECTION SUMMARY (safety cap audit: never exceed red-window counts) ---
        red_req_total = sum(r.get("count", 0) for r in vehicle_requirements)
        display_info(
            f"📋 Selection {mission_id}: {len(used_vehicle_ids)} selected | "
            f"red-window reqs {red_req_total} | patients {patients_count} | "
            f"water {req_water} foam {req_foam}"
        )

        # --- SEND ---
        # Try AAR API first if enabled (faster, avoids checkbox flakiness), else click button
        dispatched = False
        if used_vehicle_ids:
            dispatched = await _post_alarm(page, mission_id, used_vehicle_ids)
        if not dispatched:
            if len(used_vehicle_ids) == 0:
                # Diagnostic: list which requirements had no valid vehicles in garage
                try:
                    missing_types = []
                    # Build set of available vids for quick lookup
                    avail_vids = set()
                    for cb in available_vehicles_elements:
                        try:
                            v = await cb.get_attribute("value")
                            if v:
                                avail_vids.add(v)
                        except Exception:
                            continue
                    for req in vehicle_requirements:
                        if "ambulance" in req["name"].lower():
                            continue
                        valid = await get_valid_ids_for_type(req["name"])
                        if not valid:
                            missing_types.append(f"{req['name']} (no garage type)")
                        else:
                            # Check if any valid is in avail
                            if not any(vid in avail_vids for vid in valid):
                                missing_types.append(f"{req['name']} (no available, need {req['count']} have {len(valid)} types)")
                    if missing_types:
                        display_warning(f"⛔ No vehicles for {mission_id}: missing {', '.join(missing_types[:3])}{'...' if len(missing_types)>3 else ''} | have {len(avail_vids)} avail, {len(vehicle_requirements)} reqs")
                    else:
                        display_info(f"⛔ No vehicles selected for {mission_id}. Skipping dispatch click.")
                except Exception as e:
                    display_warning(f"⛔ No vehicles for {mission_id} (diag err {e})")
                    display_info(f"⛔ No vehicles selected for {mission_id}. Skipping dispatch click.")
            free_up_vehicles(mission_id)
            continue

        if dispatched:
            # Persist which vehicles were sent (freed only when the mission leaves the board)
            LOCK_MANAGER.mark_sent(used_vehicle_ids, mission_id)
            # Record the wave so the two-stage guard skips identical re-dispatch
            try:
                if get_two_stage():
                    LOCK_MANAGER.set_wave(mission_id, _mission_needs_signature(data), used_vehicle_ids)
            except Exception:
                pass

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
    # Humanized click: scroll, small jitter, then JS click + change event
    try:
        await page.evaluate('(el) => el.scrollIntoView({block: "center"})', checkbox)
    except Exception:
        try:
            await checkbox.scroll_into_view_if_needed()
        except Exception:
            pass
    await human_sleep(0.18, 0.65)
    # Occasional mouse jitter before click
    if random.random() < 0.22:
        await random_mouse_jitter(page, moves=1)
    await page.evaluate('(checkbox) => { checkbox.click(); checkbox.dispatchEvent(new Event("change", { bubbles: true })); }', checkbox)
    await human_sleep(0.12, 0.55)

async def get_valid_ids_for_type(target_name):
    user_vehicle_data = await load_vehicle_data()
    by_type = user_vehicle_data.get("by_type", {}) if isinstance(user_vehicle_data, dict) else {}
    allowed_generic_ids = VEHICLE_MANAGER.get_valid_ids(target_name)
    
    valid_ids_in_garage = []
    for allowed_id in allowed_generic_ids:
        str_id = str(allowed_id)
        if str_id in by_type:
            valid_ids_in_garage.extend(by_type[str_id])
            
    return list(set(valid_ids_in_garage))

def _mission_needs_signature(data: dict) -> str:
    """Stable signature of a mission's current needs — used by the two-stage
    guard to detect 'same wave' (skip) vs 'escalation' (phase 2 dispatch)."""
    reqs = sorted([(r.get("name", ""), r.get("count", 0)) for r in (data.get("vehicles") or [])])
    return json.dumps({
        "reqs": reqs,
        "water": int(data.get("water_needed", 0) or 0),
        "foam": int(data.get("foam_needed", 0) or 0),
        "patients": int(data.get("patients", 0) or 0),
        "crashed": int(data.get("crashed_cars", 0) or 0),
    }, sort_keys=True)

def within_dispatch_radius(d, max_disp) -> bool:
    """Radius gate: max_disp<=0 = unlimited; inf (unknown distance) always passes."""
    if not max_disp or max_disp <= 0:
        return True
    if d == float('inf'):
        return True
    return d <= max_disp

def resolve_dispatch_radius(radius_by_class, vclass, global_max) -> float:
    """G4 — per-class radius override: a class entry > 0 wins, else global."""
    try:
        cls_radius = radius_by_class.get(vclass) if radius_by_class else None
    except Exception:
        cls_radius = None
    if cls_radius is not None and cls_radius > 0:
        return cls_radius
    return global_max

def credit_unit_eligible(sys_id, fms, checked, locked, vm) -> bool:
    """G3 — pure eligibility for the credit-only alliance unit: available
    (fms 1/2), not checked/locked, not a trailer, known type."""
    if checked or locked:
        return False
    if str(fms).strip() and str(fms).strip() not in ("1", "2"):
        return False
    if sys_id is None:
        return False
    try:
        if vm.is_trailer(sys_id):
            return False
    except Exception:
        pass
    return True

def _same_station(bid_a, bid_b) -> bool:
    """Checkbox building_id can be composite ('111111_222222'). Two vehicles
    share a station if any component matches."""
    a = set(str(bid_a).split("_")) - {""}
    b = set(str(bid_b).split("_")) - {""}
    if not a or not b:
        return False
    return bool(a & b)

def trailer_local_towers(trailer_bid, towing_ids, tower_pool,
                         require_training=False, trained_map=None):
    """Eligible towing vehicles for a trailer (pure, testable).

    Rules: same station (composite building_id), fms 1/2, not checked/locked,
    type in towing_ids (when defined), crew qualified (when require_training).
    tower_pool entries: {vid, sys_id, building_id, fms, checked, locked}.
    """
    out = []
    for t in tower_pool:
        if t.get("checked") or t.get("locked"):
            continue
        if str(t.get("fms", "")).strip() not in ("1", "2"):
            continue
        if not _same_station(trailer_bid, t.get("building_id", "")):
            continue
        sys_id = t.get("sys_id")
        if towing_ids and sys_id not in towing_ids:
            continue
        if require_training and trained_map is not None and not trained_map.get(str(t.get("vid")), True):
            continue
        out.append(t)
    return out

async def _build_tower_pool(checkboxes):
    """Read (value, sys_id, building_id, fms, checked, locked) for every
    non-trailer checkbox — one pass, used by trailer eligibility checks."""
    pool = []
    for cb in checkboxes:
        try:
            v_id = await cb.get_attribute("value")
            if not v_id:
                continue
            sys_id = USER_TO_SYSTEM_MAP.get(str(v_id))
            if not sys_id:
                continue
            try:
                is_trailer = VEHICLE_MANAGER.is_trailer(sys_id) if hasattr(VEHICLE_MANAGER, 'is_trailer') else sys_id in TRAILER_IDS
            except Exception:
                is_trailer = sys_id in TRAILER_IDS
            if is_trailer:
                continue
            bid = (await cb.get_attribute("building_id")) or ""
            fms = (await cb.get_attribute("fms")) or ""
            try:
                checked = await cb.is_checked()
            except Exception:
                checked = False
            pool.append({"vid": v_id, "sys_id": sys_id, "building_id": bid,
                         "fms": fms, "checked": checked,
                         "locked": is_vehicle_locked(v_id)})
        except Exception:
            continue
    return pool

async def _post_alarm(page, mission_id, vehicle_ids) -> bool:
    """Dispatch the selected vehicles: AAR API first (if enabled), else the
    alarm button. Returns True when the dispatch was confirmed.

    Extracted so the credit-only alliance path can reuse the exact same
    sending logic (CSRF headers, payload shape, click fallback).
    """
    if not vehicle_ids:
        return False
    if get_use_aar():
        try:
            base = get_server_url().rstrip("/")
            # POST /missions/{id}/alarm with vehicle_ids[] — Rails CSRF +
            # X-Requested-With headers (token from meta tag)
            payload = {"vehicle_ids[]": vehicle_ids, "next_mission": "0"}
            req_ctx = page.request if hasattr(page, "request") else page.context.request
            try:
                from utils.api_client import post_headers
                headers = await post_headers(page)
            except Exception:
                headers = {}
            resp = await req_ctx.post(f"{base}/missions/{mission_id}/alarm", form=payload, headers=headers)
            if resp.ok:
                display_info(f"🚀 Dispatched via AAR API {mission_id} ({len(vehicle_ids)} vehicles)")
                return True
            display_warning(f"AAR dispatch failed {resp.status}: {await resp.text()[:200]} — falling back to click")
        except Exception as e:
            display_warning(f"AAR error {mission_id}: {e}")
    btn = await page.query_selector('#alert_btn')
    if not btn:
        display_warning(f"No dispatch button for {mission_id}")
        return False
    try:
        is_disabled = await btn.get_attribute("disabled")
        if is_disabled is not None:
            display_warning(f"Dispatch button disabled for {mission_id}")
            return False
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
    display_info(f"🚀 Dispatched mission {mission_id} via click ({len(vehicle_ids)} vehicles)")
    return True


async def _dispatch_credit_unit(page, mission_id) -> bool:
    """G3 — send exactly ONE eligible unit to an alliance mission (credit only).

    Picks the nearest available (fms 1/2), unlocked, non-trailer vehicle that
    is neither locked nor already checked, clicks it and fires the dispatch.
    """
    try:
        cbs = await page.query_selector_all('input.vehicle_checkbox')
    except Exception:
        return False
    candidates = []
    for cb in cbs:
        try:
            if not await cb.is_visible():
                continue
            dis = await cb.get_attribute("disabled")
            if dis is not None:
                continue
            if hasattr(cb, "is_disabled") and await cb.is_disabled():
                continue
            v_id = await cb.get_attribute("value")
            if not v_id:
                continue
            sys_id = USER_TO_SYSTEM_MAP.get(str(v_id))
            if not sys_id:
                continue
            fms = (await cb.get_attribute("fms")) or ""
            try:
                checked = await cb.is_checked()
            except Exception:
                checked = False
            if not credit_unit_eligible(sys_id, fms, checked, is_vehicle_locked(v_id), VEHICLE_MANAGER):
                continue
            candidates.append((v_id, cb))
        except Exception:
            continue
    if not candidates:
        return False
    vids = [v for v, _cb in candidates]
    dist_map = await get_vehicle_distances(page, vids)
    candidates.sort(key=lambda t: dist_map.get(t[0], float('inf')))
    vid, cb = candidates[0]
    await click_vehicle(page, cb)
    lock_vehicle(vid, mission_id)
    sent = await _post_alarm(page, mission_id, [vid])
    if sent:
        LOCK_MANAGER.mark_sent([vid], mission_id)
    else:
        unlock_vehicle(vid)
    return sent


def _crew_qualified(vm, sys_id, crew_entry, strict=False) -> bool:
    """Training gate: True if the vehicle needs no training or its assigned
    crew holds every required course.

    strict=True (G5): vehicles that DO require training fail when crew data
    is absent (scraping disabled/failed) or nobody is on board — no fail-open.
    """
    try:
        req = vm.get_required_training(sys_id) or []
    except Exception:
        req = []
    if not req:
        return True
    if not crew_entry or not isinstance(crew_entry, dict):
        return not strict
    if int(crew_entry.get("personnel", 0) or 0) <= 0:
        return not strict
    eds = [vm.normalize(e) for e in crew_entry.get("educations", []) if e]
    for r in req:
        m = re.search(r':\s*(.+?)\s*\(\d+d\)', r)
        course = m.group(1) if m else r
        cn = vm.normalize(course)
        if cn and not any(cn in e or e in cn for e in eds):
            return False
    return True