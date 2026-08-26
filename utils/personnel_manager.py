import asyncio
import re
from utils.pretty_print import display_info, display_error, display_warning
from data.config_settings import get_hiring_mode, get_server_url

async def manage_personnel(browser):
    hiring_mode = get_hiring_mode()
    
    if hiring_mode == 0:
        return

    display_info(f"👥 Starting Personnel Management (Mode: {hiring_mode})...")
    
    page = browser.contexts[0].pages[0]
    base = get_server_url().rstrip("/")
    
    try:
        # 1. Go to Main Page to get list of buildings
        if not page.url.startswith(base):
            await page.goto(f"{base}/", timeout=30000)
            await page.wait_for_load_state('domcontentloaded', timeout=15000)

        # Select all buildings from the list
        # Filtering for relevant building types (Fire, Rescue, Police, etc.)
        # building_type_id="0" is Fire, "3" is Rescue, "5" is Police
        # We generally want to check all stations that allow hiring.
        building_elements = await page.query_selector_all('.building_list_li a[href^="/buildings/"]')
        
        building_ids = []
        for el in building_elements:
            href = await el.get_attribute('href')
            if href:
                b_id = href.split('/')[-1]
                building_ids.append(b_id)
        
        # Remove duplicates
        building_ids = list(set(building_ids))
        display_info(f"Found {len(building_ids)} buildings to check.")

        # 2. Iterate through buildings
        for b_id in building_ids:
            try:
                await page.goto(f"{base}/buildings/{b_id}", timeout=30000)
                await page.wait_for_load_state('domcontentloaded', timeout=15000)
                
                # Check Personnel Count vs Target — i18n (EN/DE/FR/NL)
                personnel_dd = None
                # Try multiple language selectors
                for sel in [
                    "dl.dl-horizontal dt:has-text('Personnel:') + dd",
                    "dl.dl-horizontal dt:has-text('Personal:') + dd",
                    "dl.dl-horizontal dt:has-text('Personnel') + dd",
                    "dl.dl-horizontal dt:has-text('Personal') + dd",
                ]:
                    try:
                        loc = page.locator(sel)
                        if await loc.count() > 0:
                            personnel_dd = loc.first
                            break
                    except Exception:
                        continue
                # Fallback: any dd containing Employees/Mitarbeiter
                if not personnel_dd:
                    try:
                        loc = page.locator("dl.dl-horizontal dd")
                        cnt = await loc.count()
                        for i in range(cnt):
                            try:
                                t = await loc.nth(i).inner_text()
                                if any(k in t for k in ["Employees", "Mitarbeiter", "Personnel", "Personal"]):
                                    personnel_dd = loc.nth(i)
                                    break
                            except Exception:
                                continue
                    except Exception:
                        personnel_dd = None
                
                if personnel_dd:
                    try:
                        text = await personnel_dd.inner_text()
                    except Exception:
                        text = ""
                    # i18n: Employees (EN), Mitarbeiter (DE), Employés (FR), Medewerkers (NL)
                    current_match = re.search(r'(\d+)\s+(Employees|Mitarbeiter|Employés|Medewerkers|Werknemers|Dipendenti)', text, re.IGNORECASE)
                    if not current_match:
                        current_match = re.search(r'(\d+)\s+\w+', text)
                    target_match = re.search(r'Target:\s*(\d+)', text, re.IGNORECASE)
                    if not target_match:
                        target_match = re.search(r'Ziel:\s*(\d+)', text, re.IGNORECASE)
                    if not target_match:
                        target_match = re.search(r'(\d+)\s*Personnel', text, re.IGNORECASE)
                    
                    if current_match and target_match:
                        try:
                            current = int(current_match.group(1))
                            target = int(target_match.group(1))
                        except ValueError:
                            continue
                        
                        if current < target:
                            # Need to hire
                            await handle_hiring(page, b_id, hiring_mode)
                        else:
                            # display_info(f"Station {b_id}: Full ({current}/{target})")
                            pass
            except asyncio.CancelledError:
                raise
            except Exception as e:
                display_error(f"Error checking building {b_id}: {e}")
                continue
                
    except asyncio.CancelledError:
        raise
    except Exception as e:
        display_error(f"Error in personnel management: {e}")
    
    display_info("👥 Personnel Management finished.")

async def handle_hiring(page, building_id, mode):
    # Navigate to Hire Page
    try:
        base = get_server_url().rstrip("/")
        hire_url = f"{base}/buildings/{building_id}/hire"
        await page.goto(hire_url, timeout=30000)
        await page.wait_for_load_state('domcontentloaded', timeout=15000)
        
        # Check if recruitment is already active — i18n (EN/DE/FR)
        content = await page.content()
        active_phrases = [
            "The recruiting phase still runs for",
            "Die Einstellungsphase läuft noch",
            "La phase de recrutement",
            "De wervingsfase loopt nog",
        ]
        if any(p in content for p in active_phrases):
            # Recruitment active, skip
            return

        display_info(f"Station {building_id}: Starting recruitment...")

        # Click the appropriate button based on mode
        if mode in [1, 2, 3]:
            # Button href: /buildings/3921950/hire_do/1
            btn_selector = f"a[href='/buildings/{building_id}/hire_do/{mode}']"
            btn = await page.query_selector(btn_selector)
            if btn:
                await btn.click()
                display_info(f"Started {mode}-day recruitment for {building_id}.")
            else:
                display_error(f"Could not find {mode}-day button for {building_id}.")
                
        elif mode == -1:
            display_warning(f"Automatic hiring (Premium) not fully implemented, using 3-day for {building_id}")
            btn_selector = f"a[href='/buildings/{building_id}/hire_do/3']" # Defaulting to max
            btn = await page.query_selector(btn_selector)
            if btn:
                await btn.click()
                await page.wait_for_timeout(500)
                display_info(f"Started recruitment (Automatic/Max) for {building_id}.")

    except asyncio.CancelledError:
        raise
    except Exception as e:

        display_error(f"Failed to hire at {building_id}: {e}")
