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
        browser = await playwright.chromium.launch(headless=headless)
        page = await browser.new_page()
        base = get_server_url().rstrip("/")
        await page.goto(f"{base}/users/sign_in", timeout=30000)
        await page.wait_for_selector("form#new_user", timeout=15000)
        await page.fill('input[name="user[email]"]', username)
        await page.fill('input[name="user[password]"]', password)
        await page.click('input[type="submit"]')
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        # Wait a bit for redirect or error
        await page.wait_for_timeout(2000)
        # i18n: check still on sign_in page means failure (covers all languages)
        if "/users/sign_in" in page.url:
            # Try to find any error alert
            try:
                error_loc = page.locator('text=Invalid email or password, text=Ungültige, text=Adresse e-mail invalide, text=Ongeldig, text=Credenziali non valide, text=Nieprawidłowy, text=Neplatný, text=Ugyldig, text=Virheellinen, text=Ogiltig, text=Ugyldig, text=잘못된, text=無効な, text=Credenciales no válidas')
                if await error_loc.is_visible(timeout=3000):
                    await browser.close()
                    return "Failure", f"Thread {thread_id}: Invalid email or password", None
                # Generic: if still on sign_in, assume failure
                await browser.close()
                return "Failure", f"Thread {thread_id}: Login failed (still on sign_in)", None
            except Exception:
                await browser.close()
                return "Failure", f"Thread {thread_id}: Login failed", None

        return "Success", thread_id, browser

    except Exception as e:
        display_error(f"Thread {thread_id} encountered an error: {e}")
        if browser:
            await browser.close()
        return "Failure", f"Thread {thread_id} failed due to an unexpected error: {e}", None
