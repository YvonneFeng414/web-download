"""End-to-end orchestrator.

Flow: open article -> pick publisher adapter -> find supplement candidates
(falling back to the browser-use agent) -> for each PDF, skim first pages and
classify -> save the ones judged to be protocols above the confidence threshold.
"""

from __future__ import annotations

import os
import logging
import re
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

from openai import AsyncOpenAI

from web_download import agent_fallback
from web_download.browser import BrowserSession
from web_download.classifier import classify
from web_download.config import Settings, get_settings
from web_download.models import (
    PipelineResult,
    SupplementCandidate,
    SupplementResult,
)
from web_download.pdf_skim import fetch_and_skim
from web_download.publishers import get_adapter

log = logging.getLogger("web_download")

_SLUG = re.compile(r"[^a-z0-9]+")

load_dotenv()


def _article_id(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] or "article"


def _safe_name(article_id: str, doc_type: str, index: int) -> str:
    slug = _SLUG.sub("-", doc_type.lower()).strip("-")
    return f"{article_id}_{index:02d}_{slug}.pdf"


async def run(url: str, settings: Settings | None = None) -> PipelineResult:
    settings = settings or get_settings()
    settings.ensure_dirs()

    adapter = get_adapter(url)
    publisher = adapter.name if adapter else "unknown"
    result = PipelineResult(article_url=url, publisher=publisher)
    client = AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("BASE_URL")
    )

    async with BrowserSession(settings) as session:
        candidates: list[SupplementCandidate] = []
        if adapter is not None:
            page = await session.open(url)
            candidates = await adapter.find_supplements(page)
            log.info("adapter %s found %d candidate(s)", adapter.name, len(candidates))

        if not candidates:
            log.info("falling back to browser-use agent for %s", url)
            candidates = await agent_fallback.find_supplements(
                url, agent_model=settings.agent_model, headless=settings.headless
            )

        article_id = _article_id(url)
        for idx, cand in enumerate(candidates, start=1):
            res = SupplementResult(candidate=cand)
            result.results.append(res)

            if cand.file_type and cand.file_type != "pdf":
                res.error = f"skipped non-pdf ({cand.file_type})"
                continue

            try:
                skim = await fetch_and_skim(cand, session, settings.skim_pages)
            except Exception as exc:  # network / fetch error
                res.error = f"fetch failed: {exc}"
                continue

            if not skim.is_pdf:
                res.error = "not a PDF"
                continue
            if skim.low_text:
                res.error = "low-text PDF (possibly scanned); needs OCR — uncertain"
                continue

            try:
                decision = await classify(
                    cand.label,
                    skim.first_pages_text,
                    model=settings.classifier_model,
                    client=client,
                )
            except Exception as exc:
                res.error = f"classify failed: {exc}"
                continue
            res.decision = decision

            if (
                decision.is_protocol
                and decision.confidence >= settings.confidence_threshold
            ):
                dest = settings.downloads_dir / _safe_name(
                    article_id, decision.doc_type, idx
                )
                Path(dest).write_bytes(skim.raw)
                res.downloaded_path = str(dest)
                log.info("saved protocol -> %s", dest)

    return result
