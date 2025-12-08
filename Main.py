import asyncio
import os
import sys
from playwright.async_api import async_playwright
from setup.login import login_single
from data.config_settings import get_username, get_password, get_threads, get_headless, get_mission_delay, \
    get_transport_delay
from utils.dispatcher import navigate_and_dispatch
from utils.mission_data import check_and_grab_missions
from utils.pretty_print import display_info, display_error, display_message
from utils.transport import handle_transport_requests
from utils.vehicle_data import gather_vehicle_data


async def transport_logic(browser):
    display_info("Starting transportation logic.")
    while True:
        try:
            display_info("Handling transport requests.")
            await handle_transport_requests(browser)
            display_info("Waiting for 3 minutes before the next transport.")
            await asyncio.sleep(get_transport_delay())
        except Exception as e:
            display_error(f"Error in transport logic: {e}")


async def mission_logic(browsers_for_missions):
    display_info("Starting mission logic.")
    loop_count = 0
    while True:
        try:
            loop_count += 1
            
            # --- UPDATED LOGIC: Refresh vehicle data every 50 loops OR if file is missing ---
            should_refresh_vehicles = not os.path.exists("data/vehicle_data.json") or (loop_count % 50 == 0)
            
            if should_refresh_vehicles:
                display_info(f"Refreshing vehicle data (Loop {loop_count})...")
                await gather_vehicle_data(browsers_for_missions, len(browsers_for_missions))
            
            # Grab missions
            await check_and_grab_missions(browsers_for_missions, len(browsers_for_missions))
            
            # Dispatch
            display_info("Navigating and dispatching missions.")
            await navigate_and_dispatch(browsers_for_missions)
            
            display_info("Waiting for 10 seconds before checking missions again.")
            await asyncio.sleep(get_mission_delay())
            
        except Exception as e:
            display_error(f"Error in mission logic: {e}")

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
    display_message("Prometheus")
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

        await asyncio.gather(*tasks)
        
        for browser in browsers:
            display_info(f"Closing browser for thread: {successful_logins[browsers.index(browser)]}")
            await browser.close()

    return successful_logins, browsers

if __name__ == "__main__":
    asyncio.run(login())