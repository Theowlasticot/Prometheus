import asyncio
import json
import os
from pathlib import Path

from utils.pretty_print import display_info, display_error, display_warning
from data.config_settings import get_server_url

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = PROJECT_ROOT / "data" / "building_data.json"

async def gather_building_data(browsers, num_threads=1):
    """Gather building expansions and levels. Stores data/building_data.json.
    Lightweight: only building id, type, level, expansions list. Used for future gating.
    """
    display_info("Gathering building data...")
    try:
        page = browsers[0].contexts[0].pages[0]
        base = get_server_url().rstrip("/")
        await page.goto(f"{base}/", timeout=30000)
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        # Try to get building list from main page or /buildings
        building_ids = set()
        # Method 1: main page building_list
        try:
            elems = await page.query_selector_all('.building_list_li a[href^="/buildings/"], a[href^="/buildings/"]')
            for el in elems:
                href = await el.get_attribute('href')
                if href and "/buildings/" in href:
                    bid = href.split("/")[-1].split("?")[0].split("#")[0]
                    if bid.isdigit():
                        building_ids.add(bid)
        except Exception as e:
            display_warning(f"Building list main page failed: {e}")
        # Method 2: try /buildings page if few found
        if len(building_ids) < 5:
            try:
                await page.goto(f"{base}/buildings", timeout=30000)
                await page.wait_for_selector('a[href^="/buildings/"]', timeout=5000)
                elems = await page.query_selector_all('a[href^="/buildings/"]')
                for el in elems:
                    href = await el.get_attribute('href')
                    if href and "/buildings/" in href:
                        bid = href.split("/")[-1].split("?")[0].split("#")[0]
                        if bid.isdigit():
                            building_ids.add(bid)
                await page.goto(f"{base}/", timeout=15000)
            except Exception as e:
                display_warning(f"Buildings page fallback failed: {e}")
        building_ids = list(building_ids)
        display_info(f"Found {len(building_ids)} buildings, checking expansions...")
        if not building_ids:
            display_warning("No buildings found — skipping building data")
            return {}

        # Check expansions per building (limit to 500, concurrent when multiple browsers)
        data = {}
        if num_threads > len(building_ids):
            num_threads = len(building_ids)
        if num_threads < 1:
            num_threads = 1
        limit = min(len(building_ids), 500)
        if len(building_ids) > 500:
            display_warning(f"Found {len(building_ids)} buildings, limiting to 500 for this cycle")
        bids_slice = building_ids[:limit]

        # Helper to extract building type & expansions from a page (DRY)
        async def _extract_building_info(pg):
            expansions = []
            try:
                exp_elems = await pg.query_selector_all('.building_expansion, .label-success, .badge, dd, .dl-horizontal dd, span.label')
                for el in exp_elems:
                    try:
                        txt = (await el.inner_text()).strip()
                        if txt and len(txt) < 60 and any(k in txt.lower() for k in ["ambulance", "hazmat", "water", "airport", "forestry", "foam", "rescue", "police", "prison", "cell", "hotshot", "wildland", "forest", "coastal", "swiftwater", "boat", "airborne", "heavy machinery", "truck", "k-9", "k9", "swat", "sheriff", "fbi", "federal", "ocean", "arff"]):
                            txt_clean = txt.strip()
                            if txt_clean not in expansions:
                                expansions.append(txt_clean)
                    except Exception:
                        continue
            except Exception:
                pass
            btype = "unknown"
            try:
                t = await pg.query_selector('h1, #building_info, .building_type')
                if t:
                    btype = (await t.inner_text()).strip()[:50]
            except Exception:
                pass
            return btype, expansions

        # Use concurrent gathering when multiple browsers available
        if num_threads > 1 and len(browsers) > 1:
            async def _process_one(bid, browser):
                pg = browser.contexts[0].pages[0]
                try:
                    await pg.goto(f"{base}/buildings/{bid}", timeout=30000)
                    await pg.wait_for_load_state("domcontentloaded", timeout=10000)
                    btype, expansions = await _extract_building_info(pg)
                    return bid, {"type": btype, "expansions": expansions}
                except Exception as e:
                    display_warning(f"Building {bid} check failed: {e}")
                    return bid, None
            sem = asyncio.Semaphore(min(3, len(browsers)))
            async def _sem_task(bid, br):
                async with sem:
                    res = await _process_one(bid, br)
                    await asyncio.sleep(0.3)
                    return res
            tasks = []
            for idx, bid in enumerate(bids_slice):
                br = browsers[idx % len(browsers)]
                tasks.append(_sem_task(bid, br))
            results = await asyncio.gather(*tasks)
            for bid, info in results:
                if info is not None:
                    data[bid] = info
        else:
            for bid in bids_slice:
                try:
                    await page.goto(f"{base}/buildings/{bid}", timeout=30000)
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    btype, expansions = await _extract_building_info(page)
                    data[bid] = {"type": btype, "expansions": expansions}
                    await asyncio.sleep(0.4)
                except Exception as e:
                    display_warning(f"Building {bid} check failed: {e}")
                    continue
        # Save atomically
        os.makedirs(DATA_PATH.parent, exist_ok=True)
        tmp = str(DATA_PATH) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, str(DATA_PATH))
        display_info(f"Building data saved: {len(data)} entries")
        return data
    except asyncio.CancelledError:
        raise
    except Exception as e:
        display_error(f"Building data gather failed: {e}")
        return {}

def load_building_data():
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def has_expansion(building_data, required_expansion: str) -> bool:
    """Check if any building has required expansion (fuzzy)."""
    if not required_expansion:
        return True
    req = required_expansion.lower()
    for bid, info in building_data.items():
        for exp in info.get("expansions", []):
            if req in exp.lower() or exp.lower() in req:
                return True
    return False
