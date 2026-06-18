"""NEJM adapter (nejm.org)."""

from __future__ import annotations

from playwright.async_api import Page

from web_download.models import SupplementCandidate
from web_download.publishers.base import PublisherAdapter

_CONTAINERS = (
    "section#supplementary-material",
    "div[class*='supplementary' i]",
    "div[class*='supplement' i]",
)


class NejmAdapter(PublisherAdapter):
    name = "nejm"
    domains = ("nejm.org",)

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
