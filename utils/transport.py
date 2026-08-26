import re
import asyncio

from utils.pretty_print import display_info, display_error, display_warning

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
        try:
            await page.goto("https://www.missionchief.com")
            await page.wait_for_load_state('networkidle')
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
                    vehicle_url = f"https://www.missionchief.com/vehicles/{vehicle_id}"
                    vehicle_urls.append(vehicle_url)
                    display_info(f"Found vehicle with ID: {vehicle_id}")
            except Exception as e:
                display_warning(f"Transport: request parse error: {e}")
                continue

        for vehicle_url in vehicle_urls:
            try:
                await page.goto(vehicle_url)
                await page.wait_for_load_state('networkidle')
            except Exception as e:
                display_error(f"Transport: failed to load {vehicle_url}: {e}")
                continue

            # Try hospital table first
            hospitals_table = None
            try:
                hospitals_table = await page.query_selector('table#own-hospitals')
            except Exception:
                hospitals_table = None

            if hospitals_table:
                try:
                    hospitals = await page.query_selector_all('table#own-hospitals tbody tr')
                    display_info(f"Found {len(hospitals)} hospitals/cells")

                    smallest_distance = float('inf')
                    transport_button_to_click = None

                    for hospital in hospitals:
                        try:
                            distance_element = await hospital.query_selector('td:nth-child(2)')
                            if not distance_element:
                                continue
                            distance_text = await distance_element.inner_text()
                            distance_value = _parse_distance(distance_text)
                            if distance_value is None:
                                continue
                            transport_button = await hospital.query_selector('a.btn.btn-success')
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
                            # Look for Distance marker
                            distance_start = button_text.find("Distance: ")
                            if distance_start != -1:
                                distance_text = button_text[distance_start + len("Distance: "):]
                            else:
                                distance_text = button_text
                            distance_value = _parse_distance(distance_text)
                            if distance_value is None:
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
