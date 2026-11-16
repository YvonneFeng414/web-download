"""Verify a downloaded file is actually a study protocol / SAP.

The browser-use agent picks what it *thinks* is the protocol; this re-reads the
file's content and decides whether to keep it. **Every judgement is made by the
LLM** — there are no keyword rules, no section scores, and no thresholds that can
keep or delete a file on their own.

Rules used to do this triage, and they failed in both directions on the same
article: a figures-only appendix was kept because the CONSORT checklist bundled
into it lists the very section names a protocol has ("Specific objectives or
hypotheses", "How sample size was determined", "Where the full trial protocol can
be accessed"), while a different supplement of that article was deleted for
scoring zero. Keyword counting cannot tell a document that *is* a protocol from
one that merely *talks about* a trial.

What the LLM sees is a **digest** (:func:`build_digest`) rather than the first N
characters, because the thing we must not miss is a protocol buried deep inside a
composite supplement — ``[tables] + [figures] + [eMethods] + ... + [Study
Protocol]``. The digest is one LLM call per file and carries:

* the **front pages** verbatim — where a real protocol names itself;
* the PDF's **bookmark titles**, if it has an outline;
* a **page map** — the opening line(s) of every page (evenly sampled when the
  document is long), so a protocol starting on page 180 is still visible.

Safety rails, all of which keep the file: a non-PDF, a PDF with no extractable
text, a failed LLM call, and a negative verdict the classifier is not confident
about (``verify_reject_threshold``). We never delete what we cannot disprove.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import fitz  # PyMuPDF
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from web_download.config import Settings

log = logging.getLogger("web_download")

# Front matter included verbatim: a protocol's cover page and table of contents
# are where it identifies itself.
_FRONT_CHARS = 4_000

# Page map: how much of each page's opening to show, and how many pages to map.
# A long document is sampled at an even stride — a protocol section runs for many
# pages, so a stride well under its length still lands inside it.
_PAGE_SNIPPET_CHARS = 140
_MAX_MAPPED_PAGES = 80


class ProtocolCheck(BaseModel):
    is_protocol: bool
    doc_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


_SYSTEM = (
    "You decide whether a supplementary file from a medical-journal article IS a "
    "clinical STUDY PROTOCOL or STATISTICAL ANALYSIS PLAN (SAP).\n\n"
    "You are shown a DIGEST of the document: its front pages verbatim, its PDF "
    "bookmark titles if any, and a PAGE MAP giving the opening line(s) of each "
    "page. The page map exists so you can see the whole document's structure, not "
    "just its beginning.\n\n"
    "Set is_protocol=true when the file IS, or CONTAINS, a study protocol or an "
    "SAP. A composite supplement counts: if the page map shows a protocol or SAP "
    "section beginning anywhere inside it — even on page 180, after a hundred "
    "pages of tables — that is a keep.\n\n"
    "Set is_protocol=false for supplementary tables/figures, eMethods, "
    "investigator or site lists, consent forms, reporting checklists, and the "
    "article text itself.\n\n"
    "Two traps to avoid:\n"
    "1. A CONSORT / SPIRIT / PRISMA reporting checklist enumerates the section "
    "names a protocol contains ('Specific objectives or hypotheses', 'How sample "
    "size was determined', 'Eligibility criteria for participants', 'Where the "
    "full trial protocol can be accessed'). A checklist is a one- or two-page "
    "table of such items with item numbers and page references. It is NOT a "
    "protocol, and neither is an appendix that merely includes one.\n"
    "2. Prose that REFERS to the protocol ('according to the study protocol, "
    "patients were...') is not self-identification. A protocol states what WILL "
    "be done; a results appendix reports what WAS found.\n\n"
    "Set confidence to your calibrated confidence (0..1) IN THAT is_protocol "
    "verdict. A confident false verdict deletes the file, so when the digest is "
    "genuinely ambiguous, say so with a low confidence rather than guessing."
)


# --------------------------------------------------------------------------- #
# Text / outline extraction
# --------------------------------------------------------------------------- #
def extract_pages(path: Path, max_pages: int) -> list[str]:
    """Per-page text, or ``[]`` if the file is not a readable PDF.

    ``max_pages`` is a safety cap for a pathologically large document.
    """
    try:
        with fitz.open(path) as doc:
            return [
                doc.load_page(i).get_text("text")
                for i in range(min(max_pages, doc.page_count))
            ]
    except Exception:
        return []


def extract_first_pages_text(path: Path, max_pages: int) -> str:
    """Return text from the first ``max_pages`` pages, or "" if not a usable PDF."""
    return "\n".join(extract_pages(path, max_pages)).strip()


def pdf_outline_titles(path: Path) -> list[str]:
    """Return the PDF's bookmark/outline titles (empty if none or unreadable)."""
    try:
        with fitz.open(path) as doc:
            toc = doc.get_toc(simple=True)
    except Exception:
        return []
    return [entry[1] for entry in toc if len(entry) >= 2 and isinstance(entry[1], str)]


# --------------------------------------------------------------------------- #
# Digest
# --------------------------------------------------------------------------- #
def _sampled_indices(total: int, limit: int) -> list[int]:
    """Up to ``limit`` page indices spread evenly across ``total`` pages."""
    if total <= limit:
        return list(range(total))
    stride = total / limit
    return sorted({int(i * stride) for i in range(limit)})


def _snippet(text: str) -> str:
    """One page's opening, whitespace-collapsed to a single line."""
    return " ".join(text.split())[:_PAGE_SNIPPET_CHARS]


def build_digest(pages: list[str], outline: list[str], front_pages: int) -> str:
    """Render the whole-document view the classifier reads.

    ``pages`` is per-page text; ``front_pages`` of them are also included
    verbatim (capped at ``_FRONT_CHARS``) so a cover page and table of contents
    survive intact.
    """
    front = "\n".join(pages[:front_pages]).strip()[:_FRONT_CHARS]

    parts = [f"Pages: {len(pages)}"]

    if outline:
        titles = "\n".join(f"- {t}" for t in outline[:60])
        parts.append(f"== Bookmarks ==\n{titles}")

    parts.append(f"== First pages (verbatim) ==\n{front}")

    mapped = _sampled_indices(len(pages), _MAX_MAPPED_PAGES)
    rows = [f"p{i + 1}: {_snippet(pages[i])}" for i in mapped if pages[i].strip()]
    if rows:
        note = (
            " (sampled evenly across the document)"
            if len(mapped) < len(pages)
            else ""
        )
        parts.append(f"== Page map{note} ==\n" + "\n".join(rows))

    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# LLM classifier
# --------------------------------------------------------------------------- #
def _user_prompt(name: str, digest: str) -> str:
    return (
        f"File name: {name!r}\n\n"
        f"--- DOCUMENT DIGEST ---\n{digest}\n--- END DIGEST ---\n\n"
        "Return your structured decision."
    )


def _tokens(completion) -> int:
    usage = getattr(completion, "usage", None)
    return int(getattr(usage, "total_tokens", 0) or 0) if usage else 0


async def classify_document(
    name: str, digest: str, *, model: str, client: AsyncOpenAI
) -> tuple[ProtocolCheck, int]:
    """Classify a document digest as protocol/SAP; return (decision, tokens used)."""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_prompt(name, digest)},
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
        # Couldn't classify -> zero-confidence negative, which `decide` keeps.
        return (
            ProtocolCheck(
                is_protocol=False,
                doc_type="unknown",
                confidence=0.0,
                reason=f"verify failed: {exc}",
            ),
            0,
        )


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def decide(check: ProtocolCheck, *, reject_threshold: float) -> tuple[bool, str]:
    """Turn the classifier's verdict into a (keep, reason).

    Deleting is the only irreversible move here, so it takes a negative verdict
    the classifier is actually confident about; anything softer is kept.
    """
    if check.is_protocol:
        return True, f"kept: classifier {check.doc_type} (conf {check.confidence:.2f})"
    if check.confidence < reject_threshold:
        return True, (
            f"kept: low-confidence negative (conf {check.confidence:.2f} < "
            f"{reject_threshold:.2f}) — {check.reason}"
        )
    return False, f"rejected (conf {check.confidence:.2f}): {check.reason}"


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
async def verify_and_filter(
    paths: list[str], settings: Settings
) -> tuple[list[str], list[tuple[str, str]], int]:
    """Verify each PDF; delete only the ones the LLM judges not a protocol/SAP.

    Returns ``(kept_paths, [(deleted_path, reason), ...], verify_tokens)``. Non-PDF
    files and PDFs with no extractable text are kept unread — we don't delete what
    we can't disprove. Every other file costs exactly one LLM call.
    """
    # Built lazily so a batch of only non-PDF / no-text files never constructs a
    # client or requires an API key.
    client: AsyncOpenAI | None = None
    kept: list[str] = []
    rejected: list[tuple[str, str]] = []
    tokens = 0

    for path_str in paths:
        path = Path(path_str)
        if path.suffix.lower() != ".pdf":
            log.info("keeping non-PDF (unverified): %s", path.name)
            kept.append(path_str)
            continue

        pages = extract_pages(path, settings.verify_max_scan_pages)
        if not any(p.strip() for p in pages):
            log.info("keeping (no extractable text, unverified): %s", path.name)
            kept.append(path_str)
            continue

        digest = build_digest(pages, pdf_outline_titles(path), settings.verify_pages)

        if client is None:
            client = AsyncOpenAI(
                api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("BASE_URL")
            )
        check, used = await classify_document(
            path.name, digest, model=settings.verify_model, client=client
        )
        tokens += used

        keep, reason = decide(check, reject_threshold=settings.verify_reject_threshold)
        log.info("%s -> %s", path.name, reason)
        if keep:
            kept.append(path_str)
        else:
            try:
                path.unlink()
            except OSError:
                pass
            rejected.append((path_str, reason))

    return kept, rejected, tokens
