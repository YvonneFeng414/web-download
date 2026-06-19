"""Resolve a paper title to an article URL via DOI + Google Custom Search.

Flow: title --(regex)--> DOI --(Google Custom Search, excluding NCBI hosts)-->
article URL. Titles without a DOI are skipped (return ``None``).
"""

from __future__ import annotations

import logging
import os
import re

import requests

log = logging.getLogger("web_download")

CUSTOM_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)

# Push the publisher's own page above NCBI mirrors in the result list.
_EXCLUDE_SITES = (
    "-site:pubmed.ncbi.nlm.nih.gov "
    "-site:ncbi.nlm.nih.gov "
    "-site:pmc.ncbi.nlm.nih.gov"
)


def google_search(query: str, api_key: str, cse_id: str, **kwargs) -> dict:
    """Call the Google Custom Search JSON API and return the parsed response."""
    params = {"q": query, "key": api_key, "cx": cse_id, **kwargs}
    response = requests.get(CUSTOM_SEARCH_URL, params=params, timeout=10)
    return response.json()


def extract_doi(title: str) -> str | None:
    """Return the first DOI found in ``title``, or ``None`` if there is none."""
    match = DOI_RE.search(title)
    return match.group(0) if match else None


def resolve_article_url(
    title: str, *, api_key: str | None = None, cse_id: str | None = None
) -> str | None:
    """Resolve a paper title to its article URL, or ``None`` if unresolved.

    Returns ``None`` (and skips the search) when no DOI is present in ``title``.
    """
    doi = extract_doi(title)
    if doi is None:
        log.info("no DOI in title; skipping: %.80s", title)
        return None

    api_key = api_key or os.getenv("GOOGLE_API_KEY")
    cse_id = cse_id or os.getenv("CX_ID")

    results = google_search(f"{doi} {_EXCLUDE_SITES}", api_key, cse_id)
    items = results.get("items")
    if not items:
        log.warning("no search results for DOI %s: %s", doi, results.get("error", ""))
        return None
    return items[0]["link"]
