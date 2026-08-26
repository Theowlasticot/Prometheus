import re
import asyncio

from utils.pretty_print import display_info, display_error, display_warning
from data.config_settings import get_server_url, get_allow_alliance_hospitals, get_allow_alliance_cells, get_max_distance

def _parse_distance(text):
    """Parse distance from text like '5.3 km', '3.2 mi', 'Distance: 5.3 km (Free: 2)' handling commas."""
    if not text:
        return None
    # Find first number like 1,200 or 1.200 or 5.3
    m = re.search(r'([\d.,]+)\s*(km|mi|miles)?', text.lower())
    if not m:
        return None
    num = m.group(1).replace(',', '')
    # Handle comma decimal like "5,3 km" -> "5.3"
    # If original contains comma and dot logic: already removed commas, but "5,3" becomes "53" -> need special
    # Fallback: if original had "5,3" pattern, try replacing comma with dot before stripping
    if ',' in text and '.' not in text:
        num2 = m.group(1).replace(',', '.')
        try:
            return float(num2)
        except ValueError:
            pass
    try:
        return float(num)
    except ValueError:
        try:
            return float(text.split()[0].replace(',', '').strip())
        except ValueError:
            return None

async def handle_transport_requests(browser):
    try:
        page = browser.contexts[0].pages[0]
        base = get_server_url().rstrip("/")
        try:
            await page.goto(base, timeout=30000)
            await page.wait_for_load_state('domcontentloaded', timeout=15000)
        except Exception as e:
            display_error(f"Transport: failed to load main page: {e}")
            return

        try:
            transport_requests = await page.query_selector_all('ul#radio_messages_important li')
        except Exception as e:
            display_error(f"Transport: failed to query radio messages: {e}")
            return
        display_info(f"Found {len(transport_requests)} transport requests")

        if not transport_requests:
            return

        vehicle_urls = []
        for request in transport_requests:
            try:
                vehicle_id_element = await request.query_selector('img')
                if not vehicle_id_element:
                    continue
                vehicle_id = await vehicle_id_element.get_attribute('vehicle_id')
                if not vehicle_id:
                    # Fallback: try to parse from href or other attr
                    href = await vehicle_id_element.get_attribute('src') or ""
                    m = re.search(r'/vehicles/(\d+)', href)
                    if m:
                        vehicle_id = m.group(1)
                if vehicle_id and vehicle_id.isdigit():
                    vehicle_url = f"{base}/vehicles/{vehicle_id}"
                    vehicle_urls.append(vehicle_url)
                    display_info(f"Found vehicle with ID: {vehicle_id}")
            except Exception as e:
                display_warning(f"Transport: request parse error: {e}")
                continue

        for vehicle_url in vehicle_urls:
            try:
                await page.goto(vehicle_url, timeout=30000)
                await page.wait_for_load_state('domcontentloaded', timeout=15000)
            except Exception as e:
                display_error(f"Transport: failed to load {vehicle_url}: {e}")
                continue

            # Smart hospital/cell selection with beds, alliance toggle, max distance
            hospitals_tables = []
            try:
                allow_alliance_hosp = get_allow_alliance_hospitals()
                allow_alliance_cells = get_allow_alliance_cells()
                max_dist = get_max_distance()
            except Exception:
                allow_alliance_hosp = True
                allow_alliance_cells = True
                max_dist = 0
            # Always check own; alliance only if allowed
            for sel in ['table#own-hospitals']:
                try:
                    if await page.query_selector(sel):
                        hospitals_tables.append(sel)
                except Exception:
                    continue
            if allow_alliance_hosp or allow_alliance_cells:
                for sel in ['table#alliance-hospitals', 'table#alliance-cells', 'table#own-hospitals-alliance', 'table#alliance_hospitals', 'table#alliance_hospital']:
                    try:
                        if await page.query_selector(sel):
                            hospitals_tables.append(sel)
                    except Exception:
                        continue
            if hospitals_tables:
                try:
                    all_hospitals = []
                    for tbl in hospitals_tables:
                        try:
                            rows = await page.query_selector_all(f'{tbl} tbody tr')
                            for r in rows:
                                all_hospitals.append(r)
                        except Exception:
                            continue
                    display_info(f"Found {len(all_hospitals)} hospitals/cells (tables: {', '.join(hospitals_tables)})")

                    smallest_distance = float('inf')
                    transport_button_to_click = None

                    for hospital in all_hospitals:
                        try:
                            distance_element = await hospital.query_selector('td:nth-child(2)')
                            if not distance_element:
                                continue
                            distance_text = await distance_element.inner_text()
                            distance_value = _parse_distance(distance_text)
                            if distance_value is None:
                                continue
                            if max_dist and max_dist > 0 and distance_value > max_dist:
                                continue
                            # Check free beds — i18n: look for td containing "/" or "free"
                            free_beds = None
                            try:
                                beds_el = await hospital.query_selector('td:nth-child(3)')
                                if beds_el:
                                    beds_text = await beds_el.inner_text()
                                    m = re.search(r'(\d+)\s*/\s*(\d+)', beds_text)
                                    if m:
                                        free_beds = int(m.group(1))
                                    else:
                                        m2 = re.search(r'(\d+)', beds_text)
                                        if m2:
                                            free_beds = int(m2.group(1))
                                if free_beds is not None and free_beds == 0:
                                    continue
                            except Exception:
                                pass
                            transport_button = await hospital.query_selector('a.btn.btn-success')
                            if transport_button:
                                try:
                                    is_disabled = await transport_button.get_attribute("disabled")
                                    if is_disabled:
                                        continue
                                except Exception:
                                    pass
                            if distance_value < smallest_distance and transport_button:
                                smallest_distance = distance_value
                                transport_button_to_click = transport_button
                        except Exception:
                            continue

                    if transport_button_to_click:
                        try:
                            await transport_button_to_click.click()
                            await page.wait_for_load_state('networkidle')
                            display_info(f"Transported to nearest hospital/cell ({smallest_distance:.1f})")
                        except Exception as e:
                            display_error(f"Transport click failed: {e}")
                    else:
                        display_warning("No suitable hospital/cell button found")
                except Exception as e:
                    display_error(f"Hospital transport error: {e}")
            else:
                # Patrol / cell fallback: look for any success buttons with Distance
                try:
                    patrol_buttons = await page.query_selector_all('a.btn.btn-success')
                    display_info(f"Found {len(patrol_buttons)} patrol transport buttons")

                    smallest_distance = float('inf')
                    transport_button_to_click = None

                    for button in patrol_buttons:
                        try:
                            button_text = await button.inner_text()
                            # i18n: Distance / Entfernung / Afstand
                            distance_text = button_text
                            for marker in ["Distance:", "Entfernung:", "Afstand:", "Distance :", "Entfernung :"]:
                                if marker.lower() in button_text.lower():
                                    idx = button_text.lower().find(marker.lower())
                                    distance_text = button_text[idx + len(marker):]
                                    break
                            distance_value = _parse_distance(distance_text)
                            if distance_value is None:
                                continue
                            if max_dist and max_dist > 0 and distance_value > max_dist:
                                continue
                            if distance_value < smallest_distance:
                                smallest_distance = distance_value
                                transport_button_to_click = button
                        except Exception:
                            continue

                    if transport_button_to_click:
                        try:
                            await transport_button_to_click.click()
                            await page.wait_for_load_state('networkidle')
                            display_info(f"Transported via patrol button ({smallest_distance:.1f})")
                        except Exception as e:
                            display_error(f"Patrol transport click failed: {e}")
                    else:
                        # Release prisoners if no cell
                        try:
                            release_button = await page.query_selector('a.btn.btn-xs.btn-danger')
                            if release_button:
                                await release_button.click()
                                await page.wait_for_load_state('networkidle')
                                display_info("No cells available, clicked 'Release Prisoners'")
                            else:
                                display_info("No transport buttons or 'Release Prisoners' button found.")
                        except Exception as e:
                            display_error(f"Release button error: {e}")
                except Exception as e:
                    display_error(f"Patrol transport error: {e}")
                    continue

        display_info("Handled transport requests for all vehicles.")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        display_error(f"Error in handle_transport_requests: {e}")
