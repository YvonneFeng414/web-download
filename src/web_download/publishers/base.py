"""Publisher adapter base class and shared helpers.

An adapter knows how to (a) recognize an article URL for its publisher and
(b) extract supplementary-file candidates from the rendered page. Adding a new
publisher means subclassing :class:`PublisherAdapter` and registering it in
``publishers/__init__.py`` — the pipeline core stays unchanged.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from urllib.parse import urljoin, urlparse

from playwright.async_api import Page

from web_download.models import SupplementCandidate

# Link text that usually marks a supplement (used by the generic scan helper).
_SUPP_HINT = re.compile(
    r"supplement|supporting|appendix|protocol|analysis plan|additional file|emethods",
    re.IGNORECASE,
)
_FILE_EXT = re.compile(r"\.(pdf|docx?|xlsx?|zip)(?:\?|#|$)", re.IGNORECASE)


def file_type_of(url: str) -> str:
    m = _FILE_EXT.search(url)
    return m.group(1).lower() if m else ""


def absolutize(base_url: str, href: str) -> str:
    return urljoin(base_url, href)


def host_matches(url: str, *domains: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


class PublisherAdapter(ABC):
    name: str
    domains: tuple[str, ...]

    def matches(self, url: str) -> bool:
        return host_matches(url, *self.domains)

    @abstractmethod
    async def find_supplements(self, page: Page) -> list[SupplementCandidate]:
        """Return supplementary-file candidates found on ``page``."""

    # --- shared helpers available to subclasses -------------------------------

    async def _collect_links(
        self, page: Page, container_selector: str | None
    ) -> list[SupplementCandidate]:
        """Collect downloadable links, optionally scoped to a container.

        Keeps only links that point at a recognized file type. The link text is
        captured as the label (a strong signal for the classifier).
        """
        scope = page.locator(container_selector) if container_selector else page
        anchors = scope.locator("a[href]")
        out: list[SupplementCandidate] = []
        seen: set[str] = set()
        count = await anchors.count()
        for i in range(count):
            a = anchors.nth(i)
            href = await a.get_attribute("href")
            if not href:
                continue
            url = absolutize(page.url, href)
            ftype = file_type_of(url)
            label = (await a.inner_text() or "").strip()
            if not ftype and not _SUPP_HINT.search(label):
                continue
            if url in seen:
                continue
            seen.add(url)
            out.append(
                SupplementCandidate(
                    label=label or url.rsplit("/", 1)[-1],
                    url=url,
                    file_type=ftype or "pdf",
                    source="adapter",
                )
            )
        return out
