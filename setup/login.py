import asyncio
import random

from utils.pretty_print import display_info, display_error
from data.config_settings import get_server_url
from utils.humanize import STEALTH_SCRIPT, random_viewport, random_user_agent, human_type, human_click, jitter

async def login_single(username, password, headless, thread_id, delay, playwright):
    display_info(f"Starting login for browser: {thread_id}")
    if delay:
        # Humanized stagger with jitter ±45% to break perfect 2s steps
        human_delay = jitter(delay, 0.45) if delay > 0 else delay
        display_info(f"Staggering login for browser {thread_id} by {human_delay:.1f}s (base {delay}s)")
        await asyncio.sleep(human_delay)
    browser = None
    try:
        # Humanized browser context (stealth + random viewport/UA)
        vw = random_viewport()
        ua = random_user_agent()
        browser = await playwright.chromium.launch(headless=headless)
        # Use fresh context with humanized viewport/UA + stealth
        context = await browser.new_context(
            viewport={"width": vw[0], "height": vw[1]},
            user_agent=ua,
            locale="en-US",
            timezone_id="America/New_York",
        )
        await context.add_init_script(STEALTH_SCRIPT)
        page = await context.new_page()
        base = get_server_url().rstrip("/")
        await page.goto(f"{base}/users/sign_in", timeout=30000)
        await page.wait_for_selector("form#new_user", timeout=15000)
        # Humanized but reliable: small jitter + fill (not per-char) + click
        await page.wait_for_timeout(int(jitter(380, 0.45)))
        # Subtle mouse move
        try:
            await page.mouse.move(220 + random.randint(-18, 18), 175 + random.randint(-12, 12), steps=random.randint(5, 9))
            await asyncio.sleep(jitter(0.12, 0.6))
        except Exception:
            pass
        await page.fill('input[name="user[email]"]', username)
        await asyncio.sleep(jitter(0.18, 0.55))
        await page.fill('input[name="user[password]"]', password)
        await asyncio.sleep(jitter(0.22, 0.5))
        await page.click('input[type="submit"]')
        await asyncio.sleep(jitter(0.15, 0.4))
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        # Wait a bit for redirect or error — humanized
        await page.wait_for_timeout(int(jitter(1850, 0.35)))
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
