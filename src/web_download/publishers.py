"""Per-publisher task-prompt construction.

The agent task is assembled from a shared body (``Settings.prompt_path``) plus
ONE publisher fragment (``Settings.publisher_notes_dir/<key>.txt``) — so a JAMA
run never carries NEJM/Lancet/BMJ navigation hints into its context. The
publisher can be passed explicitly (``build_task_prompt(..., publisher="bmj")``,
``--publisher bmj``) or left to ``detect_publisher``.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from web_download.config import Settings
from web_download.resolve import extract_doi

log = logging.getLogger("web_download")

PUBLISHERS = ("jama", "nejm", "lancet", "bmj")

# Fragment used when the publisher is unknown or has no file of its own.
GENERIC = "generic"

# Article-page hosts. Matched as host == h or host.endswith("." + h), so
# www.nejm.org and bmjopen.bmj.com resolve too.
_HOSTS = {
    "jamanetwork.com": "jama",
    "nejm.org": "nejm",
    "thelancet.com": "lancet",
    "bmj.com": "bmj",
}

# Batch URLs are bare https://doi.org/<doi> — no publisher in the host — so the
# DOI prefix is the real signal there. 10.1016 is Elsevier-wide (e.g.
# 10.1016/j.jpeds...), so only the Lancet's own suffix maps to "lancet" below.
_DOI_PREFIXES = {
    "10.1001": "jama",
    "10.1056": "nejm",
    "10.1136": "bmj",
}

_LANCET_DOI_PREFIX = "10.1016/s0140-6736"


def detect_publisher(url: str, doi: str | None = None) -> str | None:
    """Infer the publisher key from an article URL, else its DOI.

    Returns ``None`` when neither identifies a publisher we have notes for.
    """
    host = (urlparse(url).hostname or "").lower().lstrip(".")
    for candidate, key in _HOSTS.items():
        if host == candidate or host.endswith(f".{candidate}"):
            return key

    doi = doi or extract_doi(url)
    if doi is None:
        return None
    doi = doi.lower()
    if doi.startswith(_LANCET_DOI_PREFIX):
        return "lancet"
    return _DOI_PREFIXES.get(doi.split("/", 1)[0])


@lru_cache(maxsize=None)
def _read_notes(notes_dir: Path, key: str) -> str:
    """Read one fragment, falling back to the generic one if it is missing."""
    path = notes_dir / f"{key}.txt"
    if not path.exists():
        if key != GENERIC:
            log.info("no notes for publisher %r; using %s notes", key, GENERIC)
            return _read_notes(notes_dir, GENERIC)
        return ""
    return path.read_text(encoding="utf-8").rstrip("\n")


def load_notes(key: str | None, notes_dir: Path) -> str:
    """Return the prompt fragment for ``key`` (generic when unknown/None)."""
    return _read_notes(notes_dir, key or GENERIC)


def load_prompt(prompt_path: Path, url: str, notes: str = "") -> str:
    """Read the prompt body and substitute ``{url}`` / ``{publisher_notes}``.

    The body is formatted exactly once; ``notes`` is inserted as plain text, so a
    stray brace in a fragment is harmless (one in the body is not).
    """
    body = prompt_path.read_text(encoding="utf-8")
    return body.format(url=url, publisher_notes=notes)


def build_task_prompt(
    url: str,
    settings: Settings,
    *,
    publisher: str | None = None,
    doi: str | None = None,
) -> str:
    """Assemble the agent task for ``url``, carrying one publisher's notes only.

    ``publisher`` wins when given; otherwise it is detected from the URL/DOI.
    """
    key = publisher or detect_publisher(url, doi)
    log.info("publisher notes: %s", key or GENERIC)
    notes = load_notes(key, settings.publisher_notes_dir)
    return load_prompt(settings.prompt_path, url, notes)
