import asyncio
import re
from utils.pretty_print import display_info, display_error
from data.config_settings import get_hiring_mode

async def manage_personnel(browser):
    hiring_mode = get_hiring_mode()
    
    if hiring_mode == 0:
        return

    display_info(f"👥 Starting Personnel Management (Mode: {hiring_mode})...")
    
    page = browser.contexts[0].pages[0]
    
    try:
        # 1. Go to Main Page to get list of buildings
        if page.url != "https://www.missionchief.com/":
            await page.goto("https://www.missionchief.com/")
            await page.wait_for_load_state('networkidle')

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
                await page.goto(f"https://www.missionchief.com/buildings/{b_id}")
                await page.wait_for_load_state('networkidle')
                
                # Check Personnel Count vs Target
                # Use locator for :has-text support, fallback to JS evaluation
                personnel_dd = None
                try:
                    loc = page.locator("dl.dl-horizontal dt:has-text('Personnel:') + dd")
                    if await loc.count() > 0:
                        personnel_dd = loc.first
                except Exception:
                    personnel_dd = None
                
                if personnel_dd:
                    try:
                        text = await personnel_dd.inner_text()
                    except Exception:
                        text = ""
                    # Parse "27 Employees" and "Target: 300"
                    current_match = re.search(r'(\d+)\s+Employees', text)
                    target_match = re.search(r'Target:\s*(\d+)', text)
                    
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
    # The button is usually "/buildings/{id}/hire"
    try:
        hire_url = f"https://www.missionchief.com/buildings/{building_id}/hire"
        # We can also find the button "Hire new people"
        await page.goto(hire_url)
        
        # Check if recruitment is already active
        # HTML: "The recruiting phase still runs for 1 day(s)."
        content = await page.content()
        if "The recruiting phase still runs for" in content:
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
