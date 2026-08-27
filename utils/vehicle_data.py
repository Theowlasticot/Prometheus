import asyncio
import json
import os
import random
import re
from pathlib import Path
from utils.pretty_print import display_info, display_error
from data.config_settings import get_server_url
from utils.humanize import jitter, human_sleep, random_mouse_jitter

PROJECT_ROOT = Path(__file__).resolve().parent.parent

async def gather_vehicle_data(browsers, num_threads):
    vehicle_ids = []
    
    # 1. Scrape all Vehicle IDs from the main list
    display_info("Scraping main vehicle list to find IDs...")
    try:
        page = browsers[0].contexts[0].pages[0]
        base = get_server_url().rstrip("/")
        await page.goto(f"{base}/vehicles", timeout=30000)
        await page.wait_for_selector("tbody tr, .pagination", timeout=10000)
        
        # Determine total pages
        total_pages = 1
        try:
            pagination = await page.query_selector_all('.pagination li a')
            if pagination and len(pagination) >= 2:
                last_page_href = await pagination[-2].get_attribute('href')
                if last_page_href and "page=" in last_page_href:
                    total_pages = int(last_page_href.split('page=')[-1])
        except (ValueError, IndexError, AttributeError) as e:
            display_error(f"Pagination parse failed, assuming 1 page: {e}")

        display_info(f"Found {total_pages} pages of vehicles.")

        for p in range(1, total_pages + 1):
            if p > 1:
                await page.goto(f"{base}/vehicles?page={p}", timeout=30000)
                await page.wait_for_selector("tbody tr", timeout=10000)
            
            rows = await page.query_selector_all('tbody tr')
            for row in rows:
                try:
                    link_elem = await row.query_selector('a[href^="/vehicles/"]')
                    if not link_elem: continue
                    href = await link_elem.get_attribute('href')
                    if not href: continue
                    v_id = href.split('/')[-1]
                    if v_id.isdigit():
                        vehicle_ids.append(v_id)
                except (AttributeError, ValueError) as e:
                    display_error(f"Row parse error: {e}")
                    continue
                
    except Exception as e:
        display_error(f"Error gathering IDs: {e}")
        return

    display_info(f"Found {len(vehicle_ids)} vehicles. Fetching system IDs...")

    # 2. Split work
    if num_threads > len(vehicle_ids): num_threads = len(vehicle_ids)
    if num_threads < 1: num_threads = 1

    chunk_size = len(vehicle_ids) // num_threads + 1
    chunks = [vehicle_ids[i:i + chunk_size] for i in range(0, len(vehicle_ids), chunk_size)]
    
    tasks = []
    for i in range(len(browsers)):
        if i < len(chunks):
            tasks.append(process_vehicle_chunk(browsers[i], chunks[i], i+1))
            
    results = await asyncio.gather(*tasks)
    
    # 3. Merge Data (Key will now be the TYPE ID, e.g. "5" for Ambulance)
    final_data = {}
    for res in results:
        for v_type_id, ids in res.items():
            if v_type_id not in final_data:
                final_data[v_type_id] = []
            final_data[v_type_id].extend(ids)

    # 4. Save atomically
    os.makedirs(PROJECT_ROOT / 'data', exist_ok=True)
    tmp_path = PROJECT_ROOT / 'data' / 'vehicle_data.json.tmp'
    final_path = PROJECT_ROOT / 'data' / 'vehicle_data.json'
    with open(tmp_path, 'w') as f:
        json.dump(final_data, f, indent=4)
    os.replace(tmp_path, final_path)
        
    display_info(f"Vehicle data refreshed. Saved {len(final_data)} unique vehicle types.")

async def process_vehicle_chunk(browser, vehicle_ids, thread_id):
    page = browser.contexts[0].pages[0]
    local_data = {} # { "5": [vid1, vid2] }
    
    total = len(vehicle_ids)
    for index, v_id in enumerate(vehicle_ids):
        if index % 20 == 0:
            display_info(f"Thread {thread_id}: Processing {index}/{total}")
        # Humanized delay between vehicle pages (0.32-0.85s vs fixed)
        await human_sleep(0.38, 0.62)
        if random.random() < 0.08:
            await random_mouse_jitter(page, moves=1)
            
        try:
            base = get_server_url().rstrip("/")
            await page.goto(f"{base}/vehicles/{v_id}", timeout=30000)
            await human_sleep(0.42, 0.45)
            
            type_id = None
            
            # Method 1: Extract ID from the 'Vehicle type' link (e.g. /fahrzeugfarbe/5)
            try:
                type_link = await page.query_selector('#vehicle-attr-type a')
                if type_link:
                    href = await type_link.get_attribute('href')
                    if href:
                        match = re.search(r'/(\d+)$', href)
                        if match:
                            type_id = match.group(1)
            except (AttributeError, ValueError) as e:
                display_error(f"Type link parse error for {v_id}: {e}")

            # Method 2: Fallback to Image attribute
            if not type_id:
                try:
                    img = await page.query_selector('img.vehicle_image_reload')
                    if img:
                        type_id = await img.get_attribute('vehicle_type_id')
                except (AttributeError, ValueError) as e:
                    display_error(f"Image type parse error for {v_id}: {e}")

            if type_id:
                if type_id not in local_data:
                    local_data[type_id] = []
                local_data[type_id].append(v_id)
            
        except Exception as e:
            display_error(f"Error processing vehicle {v_id}: {e}")
            continue
            
    return local_data
