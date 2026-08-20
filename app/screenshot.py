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


async def capture_mobile_screenshot(url: str, *, timeout_ms: int = 15_000) -> bytes:
    """Load `url` in headless Chromium at a mobile viewport and return a PNG.

    Raises whatever Playwright raises (timeout, DNS failure, etc.) — the
    caller (worker.py's handler) is responsible for catching and logging
    rather than crashing the job loop, same as every other queue handler in
    this app that talks to something outside our own infra.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(
                viewport=MOBILE_VIEWPORT,
                device_scale_factor=2,  # sharp on real (retina-class) phone screens
                is_mobile=True,
            )
            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            # Full page, not just the first viewport — the Themes page card
            # shows a short window of this and slowly pans down through the
            # rest on hover, so there needs to be real content below the fold
            # to reveal, not just the header repeated.
            return await page.screenshot(type="png", full_page=True)
        finally:
            await browser.close()
