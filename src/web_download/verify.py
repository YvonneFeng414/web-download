"""Verify a downloaded file is actually a study protocol / SAP.

The browser-use agent picks what it *thinks* is the protocol; this re-reads the
file's content and decides whether to keep it. The decision is deliberately
conservative — it combines two signals:

1. a **fact**: does the document plainly identify itself as a protocol/SAP
   (an explicit cover/heading marker)?
2. the **LLM classifier**: ``is_protocol`` plus a ``confidence`` in that verdict.

A file is deleted ONLY when there is no marker AND the classifier confidently says
it is not a protocol. Everything else is kept.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import fitz  # PyMuPDF
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from web_download.config import Settings

log = logging.getLogger("web_download")

_MAX_CHARS = 6_000

# How much of the document start counts as its "cover" for the fact check.
_COVER_CHARS = 1_500

# Strong, self-identifying phrases. Bare "protocol" is excluded on purpose to
# avoid false positives (e.g. "per-protocol analysis" in a results appendix).
_MARKER_PHRASES = (
    "study protocol",
    "trial protocol",
    "clinical study protocol",
    "clinical trial protocol",
    "statistical analysis plan",
)

_SYSTEM = (
    "You verify whether a downloaded supplementary file from a medical-journal "
    "article is a clinical STUDY PROTOCOL or STATISTICAL ANALYSIS PLAN (SAP), "
    "versus other content (eMethods, supplementary tables/figures, consent forms, "
    "the article text, etc.). A study protocol describes trial design, objectives, "
    "eligibility, interventions, endpoints, and procedures; an SAP describes the "
    "planned statistical analyses. Decide from the provided text. Set "
    "is_protocol=true ONLY for a study protocol or statistical analysis plan, and "
    "set confidence to your calibrated confidence (0..1) IN THAT is_protocol "
    "verdict."
)


class ProtocolCheck(BaseModel):
    is_protocol: bool
    doc_type: str
    confidence: float = Field(ge=0.0, le=1.0)
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


def has_protocol_marker(text: str) -> bool:
    """True if the document's cover region explicitly names itself a protocol/SAP."""
    cover = re.sub(r"\s+", " ", text[:_COVER_CHARS]).lower()
    return any(phrase in cover for phrase in _MARKER_PHRASES)


def decide(
    check: ProtocolCheck, has_marker: bool, reject_threshold: float
) -> tuple[bool, str]:
    """Combine the fact + classifier into a (keep, reason) decision.

    Reject only when there is no explicit marker AND the classifier says
    not-protocol AND it is confident (>= ``reject_threshold``).
    """
    if has_marker:
        return True, "kept: explicit protocol/SAP marker in document"
    if check.is_protocol:
        return True, f"kept: classifier {check.doc_type} (conf {check.confidence:.2f})"
    if check.confidence < reject_threshold:
        return True, (
            f"kept: low-confidence negative (conf {check.confidence:.2f} < "
            f"{reject_threshold:.2f}) — {check.reason}"
        )
    return False, f"rejected (conf {check.confidence:.2f}): {check.reason}"


def _user_prompt(name: str, text: str) -> str:
    return (
        f"File name: {name!r}\n\n"
        f"First pages of the document:\n---\n{text[:_MAX_CHARS]}\n---\n\n"
        "Return your structured decision."
    )


def _tokens(completion) -> int:
    usage = getattr(completion, "usage", None)
    return int(getattr(usage, "total_tokens", 0) or 0) if usage else 0


async def classify_text(
    name: str, text: str, *, model: str, client: AsyncOpenAI
) -> tuple[ProtocolCheck, int]:
    """Classify ``text`` as protocol/SAP; return (decision, tokens used)."""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_prompt(name, text)},
    ]
    try:
        completion = await client.chat.completions.parse(
            model=model, messages=messages, response_format=ProtocolCheck, temperature=0
        )
        decision = completion.choices[0].message.parsed
        if decision is not None:
            return decision, _tokens(completion)
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
                    '"doc_type": string, "confidence": number 0..1, '
                    '"reason": string}',
                }
            ],
            temperature=0,
        )
        raw = completion.choices[0].message.content or "{}"
        start, end = raw.find("{"), raw.rfind("}")
        return ProtocolCheck.model_validate_json(raw[start : end + 1]), _tokens(completion)
    except Exception as exc:
        # Couldn't classify -> low-confidence negative (won't trigger deletion).
        return (
            ProtocolCheck(
                is_protocol=False,
                doc_type="unknown",
                confidence=0.0,
                reason=f"verify failed: {exc}",
            ),
            0,
        )


async def verify_and_filter(
    paths: list[str], settings: Settings
) -> tuple[list[str], list[tuple[str, str]], int]:
    """Verify each PDF; delete only the ones confidently judged not a protocol/SAP.

    Returns ``(kept_paths, [(deleted_path, reason), ...], verify_tokens)``. Non-PDF
    files and PDFs with no extractable text are kept (we don't delete what we can't
    disprove).
    """
    client = AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("BASE_URL")
    )
    kept: list[str] = []
    rejected: list[tuple[str, str]] = []
    tokens = 0

    for path_str in paths:
        path = Path(path_str)
        if path.suffix.lower() != ".pdf":
            log.info("keeping non-PDF (unverified): %s", path.name)
            kept.append(path_str)
            continue

        text = extract_first_pages_text(path, settings.verify_pages)
        if not text:
            log.info("keeping (no extractable text, unverified): %s", path.name)
            kept.append(path_str)
            continue

        check, used = await classify_text(
            path.name, text, model=settings.verify_model, client=client
        )
        tokens += used
        keep, reason = decide(
            check, has_protocol_marker(text), settings.verify_reject_threshold
        )
        if keep:
            log.info("%s -> %s", path.name, reason)
            kept.append(path_str)
        else:
            log.info("%s -> %s", path.name, reason)
            try:
                path.unlink()
            except OSError:
                pass
            rejected.append((path_str, reason))

    return kept, rejected, tokens
