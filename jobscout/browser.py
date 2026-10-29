from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from jobscout.config import Settings


class BrowserError(Exception):
    """Raised when a visible browser session cannot be opened."""


@contextmanager
def browser_context(
    settings: Settings,
    profile_name: str,
    headless: bool | None = None,
) -> Iterator[tuple[object, object]]:
    from playwright.sync_api import sync_playwright

    profile_dir = Path(settings.browser.profile_dir) / profile_name
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel=settings.browser.channel,
                headless=settings.browser.headless if headless is None else headless,
                slow_mo=settings.browser.slow_mo_ms,
                viewport={"width": 1440, "height": 1000},
                locale="de-DE" if profile_name == "indeed" else "en-US",
                ignore_default_args=["--enable-automation"],
                args=["--disable-blink-features=AutomationControlled"],
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
        except Exception as exc:
            raise BrowserError(
                f"Could not launch {settings.browser.channel}. "
                f"Install Chrome or run `playwright install chromium`. Details: {exc}"
            ) from exc

        context.set_default_timeout(settings.browser.page_timeout_ms)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            yield context, page
        finally:
            context.close()
