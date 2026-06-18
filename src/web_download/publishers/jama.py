"""JAMA Network adapter (jamanetwork.com).

JAMA renders supplements inside an ``ArticleDataSupplements`` widget as a list of
``div.supplement`` items, each containing a download anchor and a descriptive
label such as "Supplement 1. Trial Protocol" / "Supplement 2. Statistical
Analysis Plan". The supplement tab is collapsed by default, so the items are in
the DOM but hidden — we read ``textContent``/``href`` directly (no click needed).

Note: JAMA is behind Cloudflare; the stealth + headed browser in ``browser.py``
is required to reach this content at all.
"""

from __future__ import annotations

from playwright.async_api import Page

from web_download.models import SupplementCandidate
from web_download.publishers.base import absolutize, file_type_of, PublisherAdapter

_ITEM = "div.supplement"
_LINK = "a.supplement-download, a.supplement-link"


class JamaAdapter(PublisherAdapter):
    name = "jama"
    domains = ("jamanetwork.com",)

    async def find_supplements(self, page: Page) -> list[SupplementCandidate]:
        try:
            await page.wait_for_selector(_ITEM, state="attached", timeout=8_000)
        except Exception:
            # Supplement widget absent; fall back to a generic scan.
            return await self._collect_links(page, None)

        items = page.locator(_ITEM)
        out: list[SupplementCandidate] = []
        seen: set[str] = set()
        for i in range(await items.count()):
            item = items.nth(i)
            anchor = item.locator(_LINK).first
            if not await anchor.count():
                continue
            href = await anchor.get_attribute("href")
            if not href:
                continue
            url = absolutize(page.url, href)
            if url in seen:
                continue
            seen.add(url)

            raw_label = (await item.evaluate("el => el.textContent")) or ""
            label = _clean_label(raw_label)
            out.append(
                SupplementCandidate(
                    label=label or url.rsplit("/", 1)[-1],
                    url=url,
                    file_type=file_type_of(url) or "pdf",
                    source="adapter",
                )
            )
        return out


def _clean_label(raw: str) -> str:
    """Collapse whitespace and drop the leading "pdf" from the icon label."""
    text = " ".join(raw.split())
    if text.lower().startswith("pdf "):
        text = text[4:]
    return text.strip()
