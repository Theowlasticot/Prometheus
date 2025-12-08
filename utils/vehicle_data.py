import asyncio
import json
import os
import re
from utils.pretty_print import display_info, display_error

async def gather_vehicle_data(browsers, num_threads):
    vehicle_ids = []
    
    # 1. Scrape all Vehicle IDs from the main list
    display_info("Scraping main vehicle list to find IDs...")
    try:
        page = browsers[0].contexts[0].pages[0]
        await page.goto("https://www.missionchief.com/vehicles")
        
        # Determine total pages
        total_pages = 1
        try:
            pagination = await page.query_selector_all('.pagination li a')
            if pagination:
                last_page_href = await pagination[-2].get_attribute('href')
                if "page=" in last_page_href:
                    total_pages = int(last_page_href.split('page=')[-1])
        except: pass

        display_info(f"Found {total_pages} pages of vehicles.")

        for p in range(1, total_pages + 1):
            if p > 1: await page.goto(f"https://www.missionchief.com/vehicles?page={p}")
            
            rows = await page.query_selector_all('tbody tr')
            for row in rows:
                try:
                    link_elem = await row.query_selector('a[href^="/vehicles/"]')
                    if not link_elem: continue
                    href = await link_elem.get_attribute('href')
                    v_id = href.split('/')[-1]
                    vehicle_ids.append(v_id)
                except: continue
                
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

    # 4. Save
    with open('data/vehicle_data.json', 'w') as f:
        json.dump(final_data, f, indent=4)
        
    display_info(f"Vehicle data refreshed. Saved {len(final_data)} unique vehicle types.")

async def process_vehicle_chunk(browser, vehicle_ids, thread_id):
    page = browser.contexts[0].pages[0]
    local_data = {} # { "5": [vid1, vid2] }
    
    total = len(vehicle_ids)
    for index, v_id in enumerate(vehicle_ids):
        if index % 20 == 0:
            display_info(f"Thread {thread_id}: Processing {index}/{total}")
            
        try:
            await page.goto(f"https://www.missionchief.com/vehicles/{v_id}")
            
            type_id = None
            
            # Method 1: Extract ID from the 'Vehicle type' link (e.g. /fahrzeugfarbe/5)
            # This is the most accurate method based on your logs.
            try:
                type_link = await page.query_selector('#vehicle-attr-type a')
                if type_link:
                    href = await type_link.get_attribute('href')
                    # href might be /fahrzeugfarbe/5 or /vehicle_types/5
                    match = re.search(r'/(\d+)$', href)
                    if match:
                        type_id = match.group(1)
            except: pass

            # Method 2: Fallback to Image attribute (seen in your list view)
            if not type_id:
                try:
                    img = await page.query_selector('img.vehicle_image_reload')
                    if img:
                        type_id = await img.get_attribute('vehicle_type_id')
                except: pass

            if type_id:
                if type_id not in local_data:
                    local_data[type_id] = []
                local_data[type_id].append(v_id)
            
        except Exception:
            pass
            
    return local_data