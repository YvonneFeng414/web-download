"""Verify a downloaded file is actually a study protocol / SAP.

The browser-use agent picks what it *thinks* is the protocol; this re-reads the
file's content and decides whether to keep it. The check scans the WHOLE document
(not just the front pages) so a protocol buried deep in a composite supplement —
``[tables] + [figures] + [eMethods] + ... + [Study Protocol]`` — is still found.

Three rule-based signals are computed over the full text:

1. **header**  — does the document identify itself as a protocol/SAP (a heading,
   a "Protocol Amendment/Version N" line, or a matching PDF bookmark)?
2. **section score** — how many distinct SPIRIT-style protocol sections appear
   (objectives, endpoints, design, eligibility, statistics, schedule)?
3. **metadata** — is there a trial-metadata block (protocol number, NCT id, or a
   structured phase/design header)?

Those drive a 3-tier decision:

* **Tier 1 (keep):** a header, or enough sections to be a FULL/PARTIAL protocol.
* **Tier 2 (delete by rules):** no header, no metadata, and essentially no protocol
  sections — a plain tables/figures file. Deleted without spending an LLM call.
* **Tier 3 (LLM arbitrates):** the borderline middle — the LLM classifier decides,
  and a low-confidence negative is still kept ("when in doubt, keep").
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

# How much of the document start counts as its "cover" for the marker fact check.
_COVER_CHARS = 1_500

# A heading line is short; longer lines are prose, not a section title.
_HEADING_MAX_LEN = 70

# Strong, self-identifying phrases. Bare "protocol" is excluded on purpose to
# avoid false positives (e.g. "per-protocol analysis" in a results appendix).
_MARKER_PHRASES = (
    "study protocol",
    "trial protocol",
    "clinical study protocol",
    "clinical trial protocol",
    "statistical analysis plan",
)

# "Protocol Amendment 2.0", "Protocol Version 3", "Protocol No. 5", ...
_AMENDMENT_RE = re.compile(
    r"protocol\s+(?:amendment|version|no\.?|number|id)\s*[:\-]?\s*\d",
    re.IGNORECASE,
)
# "protocol" sitting next to amendment/version/confidential on the same line — the
# hallmark of a protocol cover page.
_PROTOCOL_NEAR_RE = re.compile(
    r"protocol\b[^\n]{0,40}\b(?:amendment|version|confidential)\b"
    r"|\b(?:amendment|version|confidential)\b[^\n]{0,40}\bprotocol\b",
    re.IGNORECASE,
)

# Distinct SPIRIT-style protocol sections. Each entry is one category; a category
# counts once no matter how many of its synonyms hit. `section_score` = number of
# distinct categories present.
_SECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("objectives", re.compile(r"\bobjectives?\b|\bstudy aims?\b|\bspecific aims?\b", re.I)),
    (
        "endpoints",
        re.compile(
            r"\b(?:primary|secondary|exploratory)\s+(?:endpoint|outcome)s?\b"
            r"|\bendpoints?\b|\boutcome measures?\b",
            re.I,
        ),
    ),
    (
        "design",
        re.compile(
            r"\bstudy design\b|\btrial design\b|\bstudy schema\b"
            r"|\brandomi[sz]ation\b|\bdouble[-\s]?blind\b|\bplacebo[-\s]?controlled\b",
            re.I,
        ),
    ),
    (
        "eligibility",
        re.compile(r"\binclusion criteria\b|\bexclusion criteria\b|\beligibility criteria\b", re.I),
    ),
    (
        "statistics",
        re.compile(
            r"\bstatistical analysis\b|\bstatistical considerations?\b"
            r"|\bhypothesis testing\b|\bsample size\b",
            re.I,
        ),
    ),
    (
        "schedule",
        re.compile(
            r"\bschedule of (?:assessments|activities|events)\b|\bstudy procedures?\b"
            r"|\bscreening phase\b|\btreatment phase\b|\bfollow[-\s]?up phase\b",
            re.I,
        ),
    ),
)
_MAX_SECTION_SCORE = len(_SECTION_PATTERNS)

# Trial-metadata block: a protocol identifier, a registry id, or a structured
# phase/design header. Kept generic — no hardcoded sponsor or protocol number.
_METADATA_RE = re.compile(
    r"\bprotocol\s+(?:no\.?|number|id|amendment)\b|\bNCT\d{8}\b|\bEudraCT\b",
    re.IGNORECASE,
)
_DESIGN_HEADER_RE = re.compile(
    r"\bphase\s*(?:[1234]|i{1,3}|iv)\b.{0,60}"
    r"\b(?:randomi[sz]ed|double[-\s]?blind|placebo[-\s]?controlled)\b"
    r"|\b(?:randomi[sz]ed|double[-\s]?blind|placebo[-\s]?controlled)\b.{0,60}"
    r"\bphase\s*(?:[1234]|i{1,3}|iv)\b",
    re.IGNORECASE | re.DOTALL,
)

FULL_PROTOCOL = "FULL_PROTOCOL"
PARTIAL_PROTOCOL = "PARTIAL_PROTOCOL"
NO_PROTOCOL = "NO_PROTOCOL"

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


class RuleSignals(BaseModel):
    """Rule-based read of a document, computed over its full text."""

    has_header: bool
    section_score: int
    has_metadata: bool
    classification: str  # FULL_PROTOCOL | PARTIAL_PROTOCOL | NO_PROTOCOL
    confidence: float = Field(ge=0.0, le=1.0)


# --------------------------------------------------------------------------- #
# Text / outline extraction
# --------------------------------------------------------------------------- #
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


def extract_all_text(path: Path, max_pages: int) -> str:
    """Return text from every page (up to ``max_pages``), or "" if not a usable PDF.

    ``max_pages`` is a safety cap for pathologically large PDFs; the whole document
    is scanned so a protocol deep inside a composite supplement is still seen.
    """
    return extract_first_pages_text(path, max_pages)


def pdf_outline_titles(path: Path) -> list[str]:
    """Return the PDF's bookmark/outline titles (empty if none or unreadable)."""
    try:
        with fitz.open(path) as doc:
            toc = doc.get_toc(simple=True)
    except Exception:
        return []
    return [entry[1] for entry in toc if len(entry) >= 2 and isinstance(entry[1], str)]


# --------------------------------------------------------------------------- #
# Rule-based signals
# --------------------------------------------------------------------------- #
def has_protocol_marker(text: str) -> bool:
    """True if the document's cover region explicitly names itself a protocol/SAP."""
    cover = re.sub(r"\s+", " ", text[:_COVER_CHARS]).lower()
    return any(phrase in cover for phrase in _MARKER_PHRASES)


def _phrase_as_heading(text: str) -> bool:
    """True if a marker phrase appears on a short (heading-like) line anywhere."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > _HEADING_MAX_LEN:
            continue
        low = line.lower()
        if any(phrase in low for phrase in _MARKER_PHRASES):
            return True
    return False


def has_protocol_header(text: str, outline_titles: list[str] | None = None) -> bool:
    """True if the document identifies itself as a protocol/SAP, scanning the whole
    document (cover marker, a heading anywhere, an amendment/version line, or a
    matching PDF bookmark)."""
    if has_protocol_marker(text) or _phrase_as_heading(text):
        return True
    if _AMENDMENT_RE.search(text) or _PROTOCOL_NEAR_RE.search(text):
        return True
    for title in outline_titles or []:
        low = title.lower()
        if any(phrase in low for phrase in _MARKER_PHRASES):
            return True
    return False


def section_score(text: str) -> int:
    """Count the distinct SPIRIT-style protocol sections present (0.._MAX)."""
    return sum(1 for _name, pat in _SECTION_PATTERNS if pat.search(text))


def has_protocol_metadata(text: str) -> bool:
    """True if a trial-metadata block (protocol id / registry id / design header)."""
    return bool(_METADATA_RE.search(text) or _DESIGN_HEADER_RE.search(text))


def score_protocol(
    text: str,
    outline_titles: list[str] | None = None,
    *,
    full_threshold: int = 4,
    partial_threshold: int = 3,
) -> RuleSignals:
    """Compute the rule-based read of a document."""
    header = has_protocol_header(text, outline_titles)
    score = section_score(text)
    metadata = has_protocol_metadata(text)

    if header and score >= full_threshold and metadata:
        classification = FULL_PROTOCOL
    elif score >= partial_threshold:
        classification = PARTIAL_PROTOCOL
    else:
        classification = NO_PROTOCOL

    confidence = score / _MAX_SECTION_SCORE
    if header:
        confidence += 0.15
    if metadata:
        confidence += 0.1
    confidence = min(1.0, round(confidence, 2))

    return RuleSignals(
        has_header=header,
        section_score=score,
        has_metadata=metadata,
        classification=classification,
        confidence=confidence,
    )


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def decide(
    signals: RuleSignals,
    check: ProtocolCheck | None,
    *,
    reject_threshold: float,
) -> tuple[bool, str]:
    """Combine the rule signals + (optional) LLM check into a (keep, reason).

    ``check`` is ``None`` when the LLM was short-circuited: either the rules already
    keep the file (Tier 1) or already reject it (Tier 2). When present, the LLM
    arbitrates the borderline middle (Tier 3), where a low-confidence negative is
    still kept.
    """
    # Tier 1 — rules keep.
    if signals.has_header:
        return True, (
            f"kept: protocol/SAP header in document (sections "
            f"{signals.section_score}/{_MAX_SECTION_SCORE})"
        )
    if signals.classification in (FULL_PROTOCOL, PARTIAL_PROTOCOL):
        return True, (
            f"kept: rules {signals.classification} (sections "
            f"{signals.section_score}/{_MAX_SECTION_SCORE}, conf {signals.confidence:.2f})"
        )

    # Tier 2 — no LLM was run, so the rules reject.
    if check is None:
        return False, (
            f"rejected by rules: no header, sections "
            f"{signals.section_score}/{_MAX_SECTION_SCORE}, no metadata"
        )

    # Tier 3 — LLM arbitrates the borderline middle.
    if check.is_protocol:
        return True, f"kept: classifier {check.doc_type} (conf {check.confidence:.2f})"
    if check.confidence < reject_threshold:
        return True, (
            f"kept: low-confidence negative (conf {check.confidence:.2f} < "
            f"{reject_threshold:.2f}) — {check.reason}"
        )
    return False, f"rejected (conf {check.confidence:.2f}): {check.reason}"


# --------------------------------------------------------------------------- #
# LLM classifier (Tier 3)
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
async def verify_and_filter(
    paths: list[str], settings: Settings
) -> tuple[list[str], list[tuple[str, str]], int]:
    """Verify each PDF; delete only the ones judged not a protocol/SAP.

    Returns ``(kept_paths, [(deleted_path, reason), ...], verify_tokens)``. Non-PDF
    files and PDFs with no extractable text are kept (we don't delete what we can't
    disprove). Only borderline files (Tier 3) spend an LLM call.
    """
    # Built lazily: a batch that is all Tier-1 keeps / Tier-2 rule-deletes never
    # needs the LLM, so we don't construct a client (or require an API key) for it.
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

        if settings.verify_whole_doc:
            text = extract_all_text(path, settings.verify_max_scan_pages)
            outline = pdf_outline_titles(path)
        else:
            text = extract_first_pages_text(path, settings.verify_pages)
            outline = []
        if not text:
            log.info("keeping (no extractable text, unverified): %s", path.name)
            kept.append(path_str)
            continue

        signals = score_protocol(
            text,
            outline,
            full_threshold=settings.verify_section_score_full,
            partial_threshold=settings.verify_section_score_partial,
        )

        strong_keep = signals.has_header or signals.classification in (
            FULL_PROTOCOL,
            PARTIAL_PROTOCOL,
        )
        # Tier 2: rules may delete a plainly protocol-free file on their own. Only
        # in whole-doc mode, where we've actually scanned the entire file.
        rules_reject = (
            settings.verify_whole_doc
            and not signals.has_header
            and not signals.has_metadata
            and signals.section_score <= settings.verify_section_score_clear_negative
        )

        check: ProtocolCheck | None = None
        if not strong_keep and not rules_reject:
            if client is None:
                client = AsyncOpenAI(
                    api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("BASE_URL")
                )
            check, used = await classify_text(
                path.name, text, model=settings.verify_model, client=client
            )
            tokens += used

        keep, reason = decide(
            signals, check, reject_threshold=settings.verify_reject_threshold
        )
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
