"""Browser lifecycle helpers.

Uses **patchright** (a stealth-patched, drop-in Playwright) so we can get past
publisher anti-bot interstitials (e.g. JAMA's Cloudflare "Just a moment..."
challenge), which vanilla Playwright cannot clear. The challenge clears reliably
only in **headed** mode, so ``Settings.headless`` defaults to False.

``BrowserSession`` owns the driver, a Chromium browser, and a context configured
for downloads. The context's ``request`` (an ``APIRequestContext``) is reused for
cookie-aware PDF fetches — once the challenge is solved, the context's Cloudflare
clearance cookie lets us GET the supplement files directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from patchright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from web_download.config import Settings

# Title substrings shown while an anti-bot interstitial is still running.
_CHALLENGE_TITLES = ("just a moment", "attention required", "verifying")


@dataclass
class BrowserSession:
    settings: Settings
    _pw: Playwright | None = None
    _browser: Browser | None = None
    context: BrowserContext | None = None

    async def __aenter__(self) -> "BrowserSession":
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.settings.headless)
        # No custom user-agent: patchright's stealth is most effective with the
        # browser's native fingerprint.
        self.context = await self._browser.new_context(accept_downloads=True)
        self.context.set_default_timeout(self.settings.timeout_ms)
        return self

    async def __aexit__(self, *exc) -> None:
        if self.context is not None:
            await self.context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()

    async def open(self, url: str) -> Page:
        assert self.context is not None, "session not started"
        page = await self.context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        await self._await_challenge(page)
        return page

    async def _await_challenge(self, page: Page, attempts: int = 15) -> None:
        """Poll until an anti-bot interstitial clears (or attempts run out)."""
        for _ in range(attempts):
            title = (await page.title()).lower()
            if not any(c in title for c in _CHALLENGE_TITLES):
                return
            await page.wait_for_timeout(2_000)

    async def fetch_bytes(self, url: str) -> bytes:
        """GET ``url`` reusing the context's cookies; returns the response body."""
        assert self.context is not None, "session not started"
        resp = await self.context.request.get(url)
        if not resp.ok:
            raise RuntimeError(f"GET {url} -> HTTP {resp.status}")
        return await resp.body()
