"""Enrich papers with journal name and publication date via the PubMed API."""

from __future__ import annotations

import logging
import time

import requests
from tqdm import tqdm

from search_papers.models import Paper

log = logging.getLogger("search_papers")

ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


def get_pubmed_data_batch(
    pmids: list[str], request_delay: float = 0.5
) -> dict[str, dict[str, str | None]]:
    """Return ``{pmid: {"journal_name", "date"}}`` for a batch of pmids."""
    params = {
        "db": "pubmed",
        "id": ",".join(str(pmid) for pmid in pmids),
        "retmode": "json",
    }
    r = requests.get(ESUMMARY_URL, params=params).json()
    time.sleep(request_delay)

    results: dict[str, dict[str, str | None]] = {}
    try:
        for pmid in r["result"]["uids"]:
            res = r["result"][pmid]
            date = None
            for stage in res.get("history", []) or []:
                if stage.get("pubstatus") == "pubmed":
                    date = stage.get("date")
            results[pmid] = {
                "journal_name": res.get("fulljournalname"),
                "date": date,
            }
    except KeyError:
        log.warning("unexpected esummary payload: %s", r)
    return results


def enrich_papers(
    papers: list[Paper], batch_size: int = 200, request_delay: float = 0.5
) -> list[Paper]:
    """Fill in ``journal_name`` and ``date`` on each paper, in place."""
    for i in tqdm(range(0, len(papers), batch_size), desc="# paper batches"):
        batch = papers[i : i + batch_size]
        batch_data = get_pubmed_data_batch(
            [p.pmid for p in batch], request_delay=request_delay
        )
        for paper in batch:
            enrichment = batch_data.get(str(paper.pmid), {})
            paper.journal_name = enrichment.get("journal_name")
            paper.date = enrichment.get("date")
    return papers
