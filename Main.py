import asyncio
import os
import sys
import time  # Added import

from playwright.async_api import async_playwright
from setup.login import login_single
from data.config_settings import get_username, get_password, get_threads, get_headless, get_mission_delay, \
    get_transport_delay, get_hiring_check_interval, get_server_code, get_server_auto_update, get_server_refresh_interval, get_server_cache_dir, get_manifest_url, get_server_manifest_url, get_server_url
from utils.dispatcher import navigate_and_dispatch
from utils.mission_data import check_and_grab_missions, check_radio_escalation
from utils.pretty_print import display_info, display_error, display_warning, display_message
from utils.transport import handle_transport_requests
from utils.vehicle_data import gather_vehicle_data
from utils.personnel_manager import manage_personnel
from utils.building_data import gather_building_data
from utils.humanize import jitter, human_sleep
import random

async def transport_logic(browser):
    display_info("Starting transportation logic.")
    while True:
        try:
            display_info("Handling transport requests.")
            await handle_transport_requests(browser)
            # Humanized transport delay with jitter ±25%
            base_delay = get_transport_delay()
            human_delay = jitter(base_delay, 0.25)
            # Occasional micro-break (2% chance extra 12-28s like human pause)
            if random.random() < 0.02:
                extra = random.uniform(12, 28)
                human_delay += extra
                display_info(f"Human break: +{extra:.0f}s")
            display_info(f"Waiting {human_delay:.1f}s before next transport.")
            await asyncio.sleep(human_delay)
        except asyncio.CancelledError:
            display_info("Transport logic cancelled.")
            raise
        except Exception as e:
            display_error(f"Error in transport logic: {e}")
            await asyncio.sleep(jitter(5, 0.6))

async def mission_logic(browsers_for_missions):
    display_info("Starting mission logic.")
    loop_count = 0
    
    # --- Tracking Variables (time-based, not just loop-count to avoid void refetch) ---
    last_personnel_check = 0
    personnel_interval = get_hiring_check_interval() # e.g. 3600 seconds
    last_remote_sync = 0
    last_vehicle_refresh = 0
    last_building_refresh = 0
    try:
        remote_interval = get_server_refresh_interval()
        remote_auto = get_server_auto_update()
    except Exception:
        remote_interval = 3600
        remote_auto = True
    project_root = os.path.dirname(os.path.abspath(__file__))
    
    while True:
        try:
            loop_count += 1
            current_time = time.time()

            # --- PHASE 1: Personnel Check Logic ---
            # Run if enough time has passed since the last check
            if current_time - last_personnel_check > personnel_interval:
                # We use the first mission browser to handle management tasks
                # This prevents all browsers from trying to manage stations at once
                await manage_personnel(browsers_for_missions[0])
                last_personnel_check = time.time()
            
            # --- Vehicle/Building Refresh Logic (time-based to avoid void refetch in loops) ---
            # Previously every 50/100 loops caused 165 vehicle fetches every 8min even if fleet unchanged.
            # Now time-based: vehicles every 3600s (1h), buildings every 7200s (2h), plus file-mtime check.
            vehicle_data_path = os.path.join(project_root, "data", "vehicle_data.json")
            building_data_path = os.path.join(project_root, "data", "building_data.json")
            # Check file mtime to avoid refetch if recently updated (e.g., manual)
            def _file_age(path):
                try:
                    return current_time - os.path.getmtime(path)
                except Exception:
                    return float('inf')
            should_refresh_vehicles = False
            if not os.path.exists(vehicle_data_path):
                should_refresh_vehicles = True
            elif current_time - last_vehicle_refresh > 3600 and _file_age(vehicle_data_path) > 3600:
                should_refresh_vehicles = True
            # Fallback loop-count for first 2 loops to ensure initial data
            if loop_count in (1, 2) and os.path.exists(vehicle_data_path):
                should_refresh_vehicles = False

            should_refresh_buildings = False
            if not os.path.exists(building_data_path):
                should_refresh_buildings = True
            elif current_time - last_building_refresh > 7200 and _file_age(building_data_path) > 7200:
                should_refresh_buildings = True
            if loop_count in (1, 2) and os.path.exists(building_data_path):
                should_refresh_buildings = False

            if should_refresh_vehicles:
                display_info(f"Refreshing vehicle data (Loop {loop_count}, age {_file_age(vehicle_data_path):.0f}s)...")
                await gather_vehicle_data(browsers_for_missions, len(browsers_for_missions))
                last_vehicle_refresh = time.time()
            if should_refresh_buildings:
                display_info(f"Refreshing building data (Loop {loop_count}, age {_file_age(building_data_path):.0f}s)...")
                try:
                    await gather_building_data(browsers_for_missions, len(browsers_for_missions))
                    last_building_refresh = time.time()
                except Exception as e:
                    display_error(f"Building data refresh failed: {e}")

            # --- Remote .mscv Sync (keep GitHub source live) ---
            if remote_auto:
                try:
                    # Re-read interval in case dashboard changed it
                    remote_interval = get_server_refresh_interval()
                    if current_time - last_remote_sync > remote_interval:
                        display_info(f"Checking remote .mscv updates for {get_server_code()} (interval {remote_interval}s)...")
                        try:
                            from utils.remote_vehicle_store import sync_code, check_remote_changes
                            # Quick check via etag first
                            chk = await asyncio.to_thread(check_remote_changes, get_manifest_url(), get_server_cache_dir())
                            if chk.get("needs_update"):
                                display_info(f"Remote manifest changed, syncing {get_server_code()}...")
                                res = await asyncio.to_thread(sync_code, get_server_code(), get_server_cache_dir(), get_manifest_url(), get_server_manifest_url())
                                display_info(f"Remote sync: {res.get('message', str(res))}")
                                # Reload VehicleManager after sync
                                try:
                                    from utils.dispatcher import reload_vehicle_manager as r1
                                    from utils.mission_data import reload_vehicle_manager as r2
                                    r1()
                                    r2()
                                except Exception:
                                    pass
                            else:
                                display_info("Remote .mscv up-to-date (304)")
                        except Exception as e:
                            display_warning(f"Remote sync check failed: {e}")
                        last_remote_sync = time.time()
                except Exception as e:
                    display_warning(f"Remote sync scheduling error: {e}")
            
            # --- Standard Mission Loop ---
            # Grab missions
            await check_and_grab_missions(browsers_for_missions, len(browsers_for_missions))
            
            # Dispatch
            display_info("Navigating and dispatching missions.")
            await navigate_and_dispatch(browsers_for_missions)
            
            # Humanized mission delay with jitter ±30%
            base_mission = get_mission_delay()
            human_mission = jitter(base_mission, 0.30)
            # Occasional longer human pause (3% chance)
            if random.random() < 0.03:
                extra = random.uniform(18, 42)
                human_mission += extra
                display_info(f"Human pause: +{extra:.0f}s")
            display_info(f"Waiting {human_mission:.1f}s before checking missions again.")
            # Escalation-aware wait: poll the radio log periodically; if a new
            # need is announced (On-Scene escalation, reinforcements), re-grab early.
            waited = 0.0
            poll_interval = 25.0
            while waited < human_mission:
                chunk = min(poll_interval, human_mission - waited)
                await asyncio.sleep(chunk)
                waited += chunk
                if waited >= human_mission:
                    break
                try:
                    pg = browsers_for_missions[0].contexts[0].pages[0]
                    await pg.goto(f"{get_server_url().rstrip('/')}/", timeout=30000)
                    await pg.wait_for_load_state("domcontentloaded", timeout=15000)
                    await pg.wait_for_timeout(700)
                    if await check_radio_escalation(pg):
                        display_info("📻 Radio escalation detected — re-grabbing missions now")
                        break
                except Exception:
                    pass
            # Small extra human jitter between loops
            await human_sleep(0.6, 0.6)
            
        except asyncio.CancelledError:
            display_info("Mission logic cancelled.")
            raise
        except Exception as e:
            display_error(f"Error in mission logic: {e}")
            # Add a small sleep here to prevent rapid-fire error loops
            await asyncio.sleep(5)

def show_menu():
    print("\n" + "╔" + "═"*35 + "╗")
    print("║       MISSIONCHIEF BOT MENU       ║")
    print("╠" + "═"*35 + "╣")
    print("║ 1. Run Missions & Transport [Def] ║")
    print("║ 2. Run Missions Only              ║")
    print("║ 3. Run Transport Only             ║")
    print("║ 4. Exit                           ║")
    print("╚" + "═"*35 + "╝")
    choice = input("Enter your choice (1-4): ").strip()
    return choice

async def login():
    # --- STARTUP BANNER ---
    display_message("Prometheus V4")
    display_info("Created by TheoDev")
    display_info("Inspired by NateSHonor project https://github.com/NatesHonor/MissionchiefBot-X")

    # --- MENU SELECTION ---
    choice = await asyncio.to_thread(show_menu)
    
    if choice == '4':
        print("Exiting...")
        sys.exit(0)

    username = get_username()
    password = get_password()
    headless = get_headless()
    threads = get_threads()
    successful_logins = []
    browsers = []
    
    async with async_playwright() as p:
        for thread_id in range(1, threads + 1):
            delay = (thread_id - 1) * 2
            result = await login_single(username, password, headless, thread_id, delay, p)
            if result[0] == "Success":
                successful_logins.append(result[1])
                browsers.append(result[2])
                display_info(f"Login successful for browser {thread_id}.")
            else:
                display_error(f"Login failed for browser {thread_id}: {result[1]}")

        if not successful_logins:
            display_error("Login failed. No browser were successfully logged in.")
            exit(1)
            
        display_info(f"All drivers logged in successfully. Threads: {', '.join(map(str, successful_logins))}")
        
        # --- TASK ALLOCATION BASED ON MENU ---
        tasks = []
        
        # Option 1: Missions & Transport (Default)
        if choice == '1' or choice == '':
            if len(browsers) < 2:
                display_error("Not enough browsers for both mission and transport logic. Need at least 2 in config.")
                exit(1)
            
            browser_for_transport = browsers[0]
            browsers_for_missions = browsers[1:]
            
            display_info(f"Mode: Missions & Transport. (1 Transport / {len(browsers_for_missions)} Mission browsers)")
            tasks.append(asyncio.create_task(mission_logic(browsers_for_missions)))
            tasks.append(asyncio.create_task(transport_logic(browser_for_transport)))

        # Option 2: Missions Only
        elif choice == '2':
            display_info(f"Mode: Missions Only. Using all {len(browsers)} browsers for missions.")
            browsers_for_missions = browsers
            tasks.append(asyncio.create_task(mission_logic(browsers_for_missions)))

        # Option 3: Transport Only
        elif choice == '3':
            display_info("Mode: Transport Only.")
            browser_for_transport = browsers[0]
            tasks.append(asyncio.create_task(transport_logic(browser_for_transport)))
            
        else:
            print("Invalid selection. Exiting.")
            exit(1)

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            display_info("Shutting down tasks...")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        except KeyboardInterrupt:
            display_info("Interrupted by user, shutting down...")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            for browser in browsers:
                try:
                    idx = browsers.index(browser)
                    tid = successful_logins[idx] if idx < len(successful_logins) else "?"
                    display_info(f"Closing browser for thread: {tid}")
                    await browser.close()
                except Exception as e:
                    display_error(f"Error closing browser: {e}")

    return successful_logins, browsers

if __name__ == "__main__":
    asyncio.run(login())