"""Mobile-viewport screenshot of a live storefront — captured by the worker
right after a publish (see queue.JOB_CAPTURE_SCREENSHOT), shown on the
Themes page card. Runs Playwright/Chromium, so it only ever runs in the
worker process, never inline in an API request (a real page load + render
takes seconds, far too slow to hold up a publish response for).
"""

import logging

from playwright.async_api import async_playwright

log = logging.getLogger(__name__)

# A real phone viewport (iPhone-class width/height), not an arbitrary guess —
# matches what the Themes page card wants to show (see theme-card.tsx).
MOBILE_VIEWPORT = {"width": 375, "height": 812}

# How far down the page to capture — roughly hero + "Shop by category", not
# the full page. A full_page capture ran the full scroll height (17,000+px
# on a long homepage, all the way past products/testimonials/footer) into
# one extremely tall, mostly-irrelevant strip; the Themes card only needs
# enough to recognize the storefront at a glance.
CAPTURE_HEIGHT = 1400


async def capture_mobile_screenshot(url: str, *, timeout_ms: int = 15_000) -> bytes:
    """Load `url` in headless Chromium at a mobile viewport and return a PNG
    clipped to CAPTURE_HEIGHT (hero + category section, not the full page).

    Raises whatever Playwright raises (timeout, DNS failure, etc.) — the
    caller (worker.py's handler) is responsible for catching and logging
    rather than crashing the job loop, same as every other queue handler in
    this app that talks to something outside our own infra.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            # The viewport itself is CAPTURE_HEIGHT tall, not MOBILE_VIEWPORT's
            # 812 — a plain (non-full-page) screenshot's `clip` can't exceed
            # the actual browser viewport (only `full_page=True` resizes the
            # viewport to the whole document first), so requesting a taller
            # clip against an 812px viewport silently clamps back down to 812.
            page = await browser.new_page(
                viewport={"width": MOBILE_VIEWPORT["width"], "height": CAPTURE_HEIGHT},
                device_scale_factor=2,  # sharp on real (retina-class) phone screens
                is_mobile=True,
            )
            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            return await page.screenshot(type="png")
        finally:
            await browser.close()
