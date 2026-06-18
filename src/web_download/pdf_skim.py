"""Download a supplement PDF and extract text from only its first few pages.

We download the whole file (PDF cross-reference tables live at the end, so HTTP
range requests are unreliable) but parse only the leading pages — enough to
decide whether the document is a protocol, while keeping classifier input small.
"""

from __future__ import annotations

from dataclasses import dataclass

import fitz  # PyMuPDF

from web_download.browser import BrowserSession
from web_download.models import SupplementCandidate


@dataclass
class SkimResult:
    raw: bytes
    first_pages_text: str
    page_count: int
    is_pdf: bool

    @property
    def low_text(self) -> bool:
        """True for likely scanned/image PDFs with little extractable text."""
        return self.is_pdf and len(self.first_pages_text.strip()) < 200


async def fetch_and_skim(
    candidate: SupplementCandidate,
    session: BrowserSession,
    skim_pages: int,
) -> SkimResult:
    raw = await session.fetch_bytes(candidate.url)

    is_pdf = raw[:5] == b"%PDF-"
    if not is_pdf:
        return SkimResult(raw=raw, first_pages_text="", page_count=0, is_pdf=False)

    text_parts: list[str] = []
    with fitz.open(stream=raw, filetype="pdf") as doc:
        page_count = doc.page_count
        for i in range(min(skim_pages, page_count)):
            text_parts.append(doc.load_page(i).get_text("text"))

    return SkimResult(
        raw=raw,
        first_pages_text="\n".join(text_parts).strip(),
        page_count=page_count,
        is_pdf=True,
    )
