import asyncio
import json
import os
import random
import re
from pathlib import Path
from utils.pretty_print import display_info, display_error, display_warning
from data.config_settings import get_server_url, get_crew_scrape
from utils.humanize import human_sleep, random_mouse_jitter

PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def scrape_vehicle_crew(page, base, v_id):
    """Visit /vehicles/{id}/zuweisung and extract the assigned crew.

    Returns {"personnel": N, "educations": [names]} or None.
    The page renders #personal_table with one row per station employee:
      - Status cell says 'In a Vehicle: <a href="/vehicles/{id}">' for crew
        assigned to THIS vehicle
      - Education column lists the member's course names (empty = none)
    """
    try:
        await page.goto(f"{base}/vehicles/{v_id}/zuweisung", timeout=30000)
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await page.wait_for_selector("#personal_table", timeout=5000)
        await page.wait_for_timeout(600)
    except Exception:
        return None
    personnel = 0
    educations = []
    try:
        rows = await page.query_selector_all('#personal_table tr[data-filterable-by]')
        for row in rows:
            try:
                status_cell = await row.query_selector('td:nth-child(3)')
                if not status_cell:
                    continue
                status_txt = (await status_cell.inner_text()).strip().lower()
                if f"/vehicles/{v_id}" not in (await status_cell.inner_html()).lower():
                    continue
                personnel += 1
                edu_cell = await row.query_selector('td:nth-child(2)')
                if edu_cell:
                    edu_txt = (await edu_cell.inner_text()).strip()
                    if edu_txt:
                        for name in re.split(r'[,;|]|\s{2,}', edu_txt):
                            name = name.strip()
                            if name and name not in educations:
                                educations.append(name)
            except Exception:
                continue
    except Exception:
        pass
    return {"personnel": personnel, "educations": educations}


async def gather_vehicle_data(browsers, num_threads):
    # --- API path first (api_mode = auto | api_v2), DOM fallback otherwise ---
    api_mode = "dom"
    try:
        from data.config_settings import get_api_mode
        api_mode = get_api_mode()
    except Exception:
        api_mode = "dom"
    if api_mode in ("auto", "api_v2"):
        try:
            from utils.api_client import gather_vehicles_via_api
            garage = await gather_vehicles_via_api(browsers[0])
            if garage and garage.get("by_type"):
                crew_scrape = get_crew_scrape()
                if crew_scrape:
                    # Enrich with zuweisung crew (educations) — API gives counts only
                    crew = await scrape_crews_for_garage(browsers, num_threads, garage)
                    garage["crew"] = crew
                _save_garage(garage)
                display_info(f"Vehicle data via API: {len(garage['by_type'])} types, "
                             f"{sum(len(v) for v in garage['by_type'].values())} vehicles")
                return
            raise RuntimeError("API garage empty")
        except Exception as e:
            if api_mode == "api_v2":
                display_error(f"API ingestion failed ({e}) — strict mode, skipping refresh")
                return
            display_warning(f"API ingestion failed ({e}) — falling back to DOM scraping")

    # --- DOM fallback path (legacy) ---
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

    # 3. Merge Data — schema: {"by_type": {type_id: [vids]}, "crew": {vid: {...}}}
    by_type = {}
    crew = {}
    for res in results:
        for v_type_id, ids in res.get("by_type", {}).items():
            if v_type_id not in by_type:
                by_type[v_type_id] = []
            by_type[v_type_id].extend(ids)
        for vid, entry in res.get("crew", {}).items():
            if entry is not None:
                crew[vid] = entry

    final_data = {"by_type": by_type, "crew": crew}

    # 4. Save atomically
    _save_garage(final_data)

    display_info(f"Vehicle data refreshed. Saved {len(by_type)} unique vehicle types, {len(crew)} crew entries.")


def _save_garage(garage):
    os.makedirs(PROJECT_ROOT / 'data', exist_ok=True)
    tmp_path = PROJECT_ROOT / 'data' / 'vehicle_data.json.tmp'
    final_path = PROJECT_ROOT / 'data' / 'vehicle_data.json'
    with open(tmp_path, 'w') as f:
        json.dump(garage, f, indent=4)
    os.replace(tmp_path, final_path)


async def scrape_crews_for_garage(browsers, num_threads, garage):
    """Zuweisung crew scrape for every vehicle in the garage, spread over browsers."""
    vids = []
    for ids in garage.get("by_type", {}).values():
        vids.extend(ids)
    if not vids:
        return garage.get("crew", {})
    if num_threads > len(vids):
        num_threads = len(vids)
    if num_threads < 1:
        num_threads = 1
    chunk_size = len(vids) // num_threads + 1
    chunks = [vids[i:i + chunk_size] for i in range(0, len(vids), chunk_size)]
    tasks = []
    for i in range(len(browsers)):
        if i < len(chunks):
            tasks.append(scrape_crew_chunk(browsers[i], chunks[i], i + 1))
    results = await asyncio.gather(*tasks)
    crew = {}
    for res in results:
        for vid, entry in res.items():
            if entry is not None:
                if vid in garage.get("crew", {}) and isinstance(garage["crew"][vid], dict):
                    entry["personnel"] = max(entry.get("personnel", 0),
                                             garage["crew"][vid].get("personnel", 0))
                crew[vid] = entry
    return crew


async def scrape_crew_chunk(browser, vids, thread_id):
    page = browser.contexts[0].pages[0]
    base = get_server_url().rstrip("/")
    out = {}
    total = len(vids)
    for index, v_id in enumerate(vids):
        if index % 25 == 0:
            display_info(f"Crew thread {thread_id}: {index}/{total}")
        await human_sleep(0.35, 0.6)
        if random.random() < 0.06:
            await random_mouse_jitter(page, moves=1)
        try:
            out[v_id] = await scrape_vehicle_crew(page, base, v_id)
        except Exception:
            out[v_id] = None
    return out


async def process_vehicle_chunk(browser, vehicle_ids, thread_id):
    page = browser.contexts[0].pages[0]
    local_by_type = {}   # { "5": [vid1, vid2] }
    local_crew = {}      # { vid: {"personnel": N, "educations": [...]} }
    base = get_server_url().rstrip("/")
    scrape_crew = get_crew_scrape()

    total = len(vehicle_ids)
    for index, v_id in enumerate(vehicle_ids):
        if index % 20 == 0:
            display_info(f"Thread {thread_id}: Processing {index}/{total}")
        # Humanized delay between vehicle pages (0.32-0.85s vs fixed)
        await human_sleep(0.38, 0.62)
        if random.random() < 0.08:
            await random_mouse_jitter(page, moves=1)

        try:
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
                if type_id not in local_by_type:
                    local_by_type[type_id] = []
                local_by_type[type_id].append(v_id)

            # Crew scrape (zuweisung page) — gated by config ingestion_settings.crew_scrape
            if scrape_crew:
                entry = await scrape_vehicle_crew(page, base, v_id)
                local_crew[v_id] = entry
                if entry and entry.get("personnel"):
                    display_info(f"Crew {v_id}: {entry['personnel']} onboard, "
                                 f"{len(entry.get('educations', []))} courses")

        except Exception as e:
            display_error(f"Error processing vehicle {v_id}: {e}")
            continue

    return {"by_type": local_by_type, "crew": local_crew}
