import asyncio

from utils.pretty_print import display_info, display_error
from data.config_settings import get_server_url

async def login_single(username, password, headless, thread_id, delay, playwright):
    display_info(f"Starting login for browser: {thread_id}")
    if delay:
        display_info(f"Staggering login for browser {thread_id} by {delay}s")
        await asyncio.sleep(delay)
    browser = None
    try:
        browser = await playwright.chromium.launch(headless=headless, devtools=False)
        page = await browser.new_page()
        base = get_server_url().rstrip("/")
        await page.goto(f"{base}/users/sign_in", timeout=30000)
        await page.wait_for_selector("form#new_user", timeout=15000)
        await page.fill('input[name="user[email]"]', username)
        await page.fill('input[name="user[password]"]', password)
        await page.click('input[type="submit"]')
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        # i18n: check multiple language error strings
        error_loc = page.locator('text=Invalid email or password, text=Ungültige E-Mail, text=Adresse e-mail invalide, text=Ongeldig e-mailadres')
        try:
            if await error_loc.is_visible(timeout=5000):
                await browser.close()
                return "Failure", f"Thread {thread_id}: Invalid email or password", None
        except Exception:
            pass

        return "Success", thread_id, browser

    except Exception as e:
        display_error(f"Thread {thread_id} encountered an error: {e}")
        if browser:
            await browser.close()
        return "Failure", f"Thread {thread_id} failed due to an unexpected error: {e}", None
