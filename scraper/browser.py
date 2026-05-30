"""
Playwright browser manager.

Provides a reusable async context manager that yields a stealth-configured
browser page. Handles retries, timeouts, and clean teardown.

Usage:
    async with managed_page() as page:
        await page.goto(url)
        html = await page.content()
"""

from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from typing import AsyncIterator

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from config.settings import settings

# ---------------------------------------------------------------------------
# User-agent pool — rotate to reduce fingerprinting
# ---------------------------------------------------------------------------
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

# Shared playwright instance (one per process)
_playwright: Playwright | None = None
_browser: Browser | None = None
_browser_lock = asyncio.Lock()


async def _get_browser() -> Browser:
    """Return (or lazily create) the shared Chromium browser instance."""
    global _playwright, _browser
    async with _browser_lock:
        if _browser is None or not _browser.is_connected():
            if _playwright is None:
                _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
    return _browser


async def close_browser() -> None:
    """Cleanly shut down the browser (call at process exit)."""
    global _playwright, _browser
    if _browser is not None:
        await _browser.close()
        _browser = None
    if _playwright is not None:
        await _playwright.stop()
        _playwright = None


@asynccontextmanager
async def managed_page(
    viewport_width: int = 1280,
    viewport_height: int = 900,
) -> AsyncIterator[Page]:
    """
    Async context manager that yields a stealth-configured browser page.
    The context (cookies, cache) is isolated per call.
    """
    browser = await _get_browser()
    ua = random.choice(_USER_AGENTS)

    context: BrowserContext = await browser.new_context(
        user_agent=ua,
        viewport={"width": viewport_width, "height": viewport_height},
        locale="en-US",
        timezone_id="America/Chicago",
        java_script_enabled=True,
        # Accept all cookies to avoid consent banners
        accept_downloads=False,
    )

    # Stealth: mask webdriver flag
    await context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        window.chrome = { runtime: {} };
        """
    )

    page: Page = await context.new_page()

    # Block unnecessary resources to speed up page loads
    await page.route(
        "**/*",
        _block_unnecessary_resources,
    )

    try:
        yield page
    finally:
        await context.close()


async def _block_unnecessary_resources(route, request) -> None:
    """Abort requests for resource types that don't affect DOM content."""
    blocked = {"font", "media"}
    # Let tracking pixels through — blocking them sometimes triggers bot detection
    if request.resource_type in blocked:
        await route.abort()
    else:
        await route.continue_()


async def fetch_page_with_retry(
    url: str,
    *,
    max_retries: int | None = None,
    scroll: bool = True,
) -> tuple[str, str]:
    """
    Navigate to `url` and return (html_content, final_url).

    Retries up to max_retries times with exponential backoff.
    Scrolls the page to trigger lazy-loaded content if scroll=True.

    Raises RuntimeError if all retries are exhausted.
    """
    max_retries = max_retries if max_retries is not None else settings.max_retries
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        if attempt > 0:
            delay = settings.retry_delay_seconds * (2 ** (attempt - 1))
            await asyncio.sleep(delay)

        try:
            async with managed_page() as page:
                await page.goto(
                    url,
                    timeout=settings.scrape_timeout_ms,
                    wait_until="domcontentloaded",
                )

                # Groupon is a React SPA — wait for real content to render
                # Try the deal title first (most reliable indicator), then fall back
                _CONTENT_SELECTORS = (
                    "[data-qa='deal-page-title']",
                    "h1.deal-title",
                    "h1",
                    "[class*='DealTitle']",
                    "[class*='deal-title']",
                )
                for sel in _CONTENT_SELECTORS:
                    try:
                        await page.wait_for_selector(sel, timeout=8_000)
                        break
                    except Exception:
                        continue

                # Extra settle time for React hydration and lazy components
                await asyncio.sleep(1.5)

                if scroll:
                    await _scroll_page(page)

                # Wait for network to quiet after scroll-triggered requests
                try:
                    await page.wait_for_load_state("networkidle", timeout=5_000)
                except Exception:
                    pass  # networkidle is best-effort; proceed regardless

                html = await page.content()
                final_url = page.url
                return html, final_url

        except Exception as exc:
            last_exc = exc
            continue

    raise RuntimeError(
        f"Failed to fetch {url} after {max_retries + 1} attempts: {last_exc}"
    )


async def _scroll_page(page: Page, steps: int = 5) -> None:
    """Scroll incrementally to trigger lazy image loading."""
    for i in range(1, steps + 1):
        await page.evaluate(
            f"window.scrollTo(0, document.body.scrollHeight * {i / steps})"
        )
        await asyncio.sleep(0.4)
    # Scroll back to top
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(0.3)
