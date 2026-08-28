"""Humanization helpers to avoid anti-bot detection."""
import asyncio
import random

# Random jitter helpers
def jitter(base: float, spread: float = 0.4) -> float:
    """Return base +/- spread*base random. e.g. jitter(10, 0.3) -> 7-13."""
    low = base * (1 - spread)
    high = base * (1 + spread)
    return random.uniform(low, high)

def jitter_int(base: int, spread: float = 0.25) -> int:
    return max(1, int(jitter(float(base), spread)))

async def human_sleep(base: float, spread: float = 0.35):
    """Sleep with jitter, like human pause."""
    await asyncio.sleep(jitter(base, spread))

def random_delay_ms(low=80, high=280) -> int:
    return random.randint(low, high)

async def human_type(page, selector: str, text: str, delay_low=45, delay_high=140):
    """Type like human with random per-char delay, occasional typo correction."""
    loc = page.locator(selector)
    await loc.click()
    # Clear first with human-like select-all + backspace occasionally
    if random.random() < 0.15:
        await page.keyboard.press("Control+A")
        await asyncio.sleep(random.uniform(0.08, 0.18))
        await page.keyboard.press("Backspace")
        await asyncio.sleep(random.uniform(0.12, 0.28))
    for ch in text:
        await page.keyboard.type(ch, delay=random.randint(delay_low, delay_high))
        # occasional micro-pause
        if random.random() < 0.07:
            await asyncio.sleep(random.uniform(0.12, 0.35))
    # occasional extra pause after typing
    await asyncio.sleep(random.uniform(0.18, 0.45))

async def human_click(page, locator_or_selector, timeout=8000):
    """Move mouse like human then click with slight offset.

    Scrolls the element into view first — a raw mouse.click at coordinates
    outside the viewport silently misses the target (no error raised).
    """
    try:
        if isinstance(locator_or_selector, str):
            loc = page.locator(locator_or_selector).first
        else:
            loc = locator_or_selector
        # Ensure in viewport — Playwright auto-scrolls on click, we do it for the mouse path
        try:
            await loc.scroll_into_view_if_needed(timeout=timeout)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(0.10, 0.25))
        box = await loc.bounding_box()
        viewport_h = None
        try:
            vs = page.viewport_size
            if vs:
                viewport_h = vs.get("height")
        except Exception:
            viewport_h = None
        # Only do the manual mouse path when the box is comfortably inside the viewport
        if box and (viewport_h is None or (0 <= box["y"] < viewport_h - 10)):
            # Move to near center with ±8px jitter
            x = box["x"] + box["width"] / 2 + random.uniform(-8, 8)
            y = box["y"] + box["height"] / 2 + random.uniform(-4, 4)
            # Human-like move in 2-3 steps
            cur = {"x": random.uniform(100, 400), "y": random.uniform(100, 300)}
            steps = random.randint(2, 4)
            for _ in range(steps):
                nx = cur["x"] + (x - cur["x"]) * random.uniform(0.35, 0.65)
                ny = cur["y"] + (y - cur["y"]) * random.uniform(0.35, 0.65)
                await page.mouse.move(nx, ny, steps=random.randint(3, 7))
                await asyncio.sleep(random.uniform(0.04, 0.12))
                cur = {"x": nx, "y": ny}
            await page.mouse.move(x, y, steps=random.randint(5, 12))
            await asyncio.sleep(random.uniform(0.06, 0.18))
            await page.mouse.click(x, y)
        else:
            # Off-viewport or no box -> rely on Playwright's auto-scrolling click
            await loc.click(timeout=timeout)
        # Small post-click pause
        await asyncio.sleep(random.uniform(0.12, 0.32))
        return True
    except Exception:
        # Fallback to direct click
        try:
            if isinstance(locator_or_selector, str):
                await page.click(locator_or_selector, timeout=timeout)
            else:
                await locator_or_selector.click(timeout=timeout)
            await asyncio.sleep(random.uniform(0.1, 0.25))
            return True
        except Exception:
            return False

async def human_scroll(page, direction="down", amount=None):
    """Random scroll like human."""
    if amount is None:
        amount = random.randint(180, 520)
    if direction == "down":
        await page.mouse.wheel(0, amount)
    else:
        await page.mouse.wheel(0, -amount)
    await asyncio.sleep(random.uniform(0.18, 0.45))

async def random_mouse_jitter(page, moves=1):
    """Small random mouse moves to look human."""
    for _ in range(moves):
        x = random.randint(80, 900)
        y = random.randint(80, 600)
        await page.mouse.move(x, y, steps=random.randint(4, 10))
        await asyncio.sleep(random.uniform(0.06, 0.18))

STEALTH_SCRIPT = """
// Hide webdriver
Object.defineProperty(navigator, 'webdriver', {get: () => undefined, configurable: true});
try { delete navigator.__proto__.webdriver; } catch(e) {}
try { delete Object.getPrototypeOf(navigator).webdriver; } catch(e) {}
// Mock plugins - must have length
Object.defineProperty(navigator, 'plugins', {get: () => { const p = [1,2,3,4,5]; p.__proto__ = PluginArray.prototype; return p; }, configurable: true});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en'], configurable: true});
Object.defineProperty(navigator, 'permissions', {get: () => ({ query: (p) => Promise.resolve({state: 'granted', onchange: null}) }), configurable: true});
// Mock chrome
window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
Object.defineProperty(window, 'chrome', {get: () => ({ runtime: {}, loadTimes: function(){}, csi: function(){} }), configurable: true});
// Hide Playwright traces
try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array; } catch(e){}
try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise; } catch(e){}
try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_JSON; } catch(e){}
delete window.__playwright;
delete window.__pw;
delete window._playwright;
"""

VIEWPORTS = [
    (1366, 768), (1920, 1080), (1440, 900), (1536, 864), (1280, 800),
    (1600, 900), (1680, 1050), (1920, 1200),
]
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

def random_viewport():
    return random.choice(VIEWPORTS)

def random_user_agent():
    return random.choice(USER_AGENTS)

def get_humanized_delays():
    """Return dict with humanized base delays. Called per loop to vary."""
    return {
        "mission": jitter(10, 0.30),  # 7-13s vs fixed 10
        "transport": jitter(60, 0.25),  # 45-75s vs 60
        "personnel": jitter(3600, 0.15),  # 3060-4140 vs 3600
        "building": jitter(7200, 0.20),
        "vehicle": jitter(3600, 0.20),
        "page_load": jitter(0.9, 0.45),  # 0.5-1.3s vs 0.3
        "dispatch_click": jitter(0.85, 0.35),
        "human_pause": jitter(1.2, 0.50),
    }
