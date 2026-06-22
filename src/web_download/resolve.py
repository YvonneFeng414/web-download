"""Resolve a paper title to an article URL via its DOI.

Flow: title --(regex)--> DOI --> ``https://doi.org/<doi>`` (the DOI resolver
redirects to the publisher's article page). Titles without a DOI are skipped
(return ``None``).
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger("web_download")

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)


def extract_doi(title: str) -> str | None:
    """Return the first DOI in ``title`` (trailing citation punctuation stripped)."""
    match = DOI_RE.search(title)
    return match.group(0).rstrip(".") if match else None


def resolve_article_url(title: str) -> str | None:
    """Resolve a paper title to its article URL, or ``None`` if it has no DOI.

    Uses the DOI resolver ``https://doi.org/<doi>``, which 302-redirects to the
    publisher's page — no API key or quota required.
    """
    doi = extract_doi(title)
    if doi is None:
        log.info("no DOI in title; skipping: %.80s", title)
        return None
    return f"https://doi.org/{doi}"
