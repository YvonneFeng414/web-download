"""Verify a downloaded file is actually a study protocol / SAP.

The browser-use agent picks what it *thinks* is the protocol; this re-reads the
file's content and asks an LLM to confirm. Files that fail are deleted, so only
genuine protocols/SAPs remain on disk.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import fitz  # PyMuPDF
from openai import AsyncOpenAI
from pydantic import BaseModel

from web_download.config import Settings

log = logging.getLogger("web_download")

_MAX_CHARS = 6_000

_SYSTEM = (
    "You verify whether a downloaded supplementary file from a medical-journal "
    "article is a clinical STUDY PROTOCOL or STATISTICAL ANALYSIS PLAN (SAP), "
    "versus other content (eMethods, supplementary tables/figures, consent forms, "
    "the article text, etc.). A study protocol describes trial design, objectives, "
    "eligibility, interventions, endpoints, and procedures; an SAP describes the "
    "planned statistical analyses. Decide from the provided text. Set "
    "is_protocol=true ONLY for a study protocol or statistical analysis plan."
)


class ProtocolCheck(BaseModel):
    is_protocol: bool
    doc_type: str
    reason: str


def extract_first_pages_text(path: Path, max_pages: int) -> str:
    """Return text from the first ``max_pages`` pages, or "" if not a usable PDF."""
    try:
        with fitz.open(path) as doc:
            parts = [
                doc.load_page(i).get_text("text")
                for i in range(min(max_pages, doc.page_count))
            ]
    except Exception:
        return ""
    return "\n".join(parts).strip()


def _user_prompt(name: str, text: str) -> str:
    return (
        f"File name: {name!r}\n\n"
        f"First pages of the document:\n---\n{text[:_MAX_CHARS]}\n---\n\n"
        "Return your structured decision."
    )


async def verify_pdf(
    path: Path, *, model: str, client: AsyncOpenAI, max_pages: int
) -> ProtocolCheck:
    """Classify a single PDF; no extractable text -> not a protocol."""
    text = extract_first_pages_text(path, max_pages)
    if not text:
        return ProtocolCheck(
            is_protocol=False, doc_type="unknown", reason="no extractable PDF text"
        )

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_prompt(path.name, text)},
    ]
    try:
        completion = await client.chat.completions.parse(
            model=model, messages=messages, response_format=ProtocolCheck, temperature=0
        )
        decision = completion.choices[0].message.parsed
        if decision is not None:
            return decision
    except Exception:
        # Proxy may not support structured outputs — fall back to JSON parsing.
        pass

    try:
        completion = await client.chat.completions.create(
            model=model,
            messages=messages
            + [
                {
                    "role": "user",
                    "content": 'Reply with ONLY JSON: {"is_protocol": bool, '
                    '"doc_type": string, "reason": string}',
                }
            ],
            temperature=0,
        )
        raw = completion.choices[0].message.content or "{}"
        start, end = raw.find("{"), raw.rfind("}")
        return ProtocolCheck.model_validate_json(raw[start : end + 1])
    except Exception as exc:
        return ProtocolCheck(
            is_protocol=False, doc_type="unknown", reason=f"verify failed: {exc}"
        )


async def verify_and_filter(
    paths: list[str], settings: Settings
) -> tuple[list[str], list[tuple[str, str]]]:
    """Verify each PDF; delete the ones that aren't a protocol/SAP.

    Returns ``(kept_paths, [(deleted_path, reason), ...])``. Non-PDF files are
    kept (we don't blindly delete e.g. a .docx protocol).
    """
    client = AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("BASE_URL")
    )
    kept: list[str] = []
    rejected: list[tuple[str, str]] = []

    for path_str in paths:
        path = Path(path_str)
        if path.suffix.lower() != ".pdf":
            log.info("keeping non-PDF (unverified): %s", path.name)
            kept.append(path_str)
            continue

        check = await verify_pdf(
            path,
            model=settings.verify_model,
            client=client,
            max_pages=settings.verify_pages,
        )
        if check.is_protocol:
            log.info("verified %s as %s", path.name, check.doc_type)
            kept.append(path_str)
        else:
            log.info("rejecting %s: %s", path.name, check.reason)
            try:
                path.unlink()
            except OSError:
                pass
            rejected.append((path_str, check.reason))

    return kept, rejected
