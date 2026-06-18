"""The Lancet adapter (thelancet.com)."""

from __future__ import annotations

from playwright.async_api import Page

from web_download.models import SupplementCandidate
from web_download.publishers.base import PublisherAdapter

_CONTAINERS = (
    "section[id*='supplementary' i]",
    "div[class*='supplementary-material' i]",
    "div[class*='supplement' i]",
)


class LancetAdapter(PublisherAdapter):
    name = "lancet"
    domains = ("thelancet.com",)

    async def find_supplements(self, page: Page) -> list[SupplementCandidate]:
        for sel in _CONTAINERS:
            try:
                await page.wait_for_selector(sel, timeout=4_000)
            except Exception:
                continue
            found = await self._collect_links(page, sel)
            if found:
                return found
        return await self._collect_links(page, None)
