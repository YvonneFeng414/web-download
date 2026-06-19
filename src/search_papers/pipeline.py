"""End-to-end orchestrator.

Flow: fetch ClinicalTrials.gov studies (sharded to disk) -> flatten study
references into papers -> enrich each paper with PubMed journal/date -> write the
enriched papers list.
"""

from __future__ import annotations

import json
import logging

from search_papers.clinicaltrials import fetch_studies, load_shards
from search_papers.config import Settings, get_settings
from search_papers.models import PipelineResult
from search_papers.preprocess import studies_to_papers
from search_papers.pubmed import enrich_papers

log = logging.getLogger("search_papers")


def run(settings: Settings | None = None, *, skip_fetch: bool = False) -> PipelineResult:
    settings = settings or get_settings()
    settings.ensure_dirs()

    if skip_fetch:
        log.info("reusing shards in %s", settings.ct_shards_dir)
        studies = load_shards(settings.ct_shards_dir)
    else:
        studies = fetch_studies(
            settings.ct_shards_dir,
            overall_status=settings.overall_status,
            fields=settings.fields,
            page_size=settings.page_size,
            max_results=settings.max_results,
            request_delay=settings.request_delay,
        )

    # studies is a generator; studies_to_papers consumes it streaming.
    studies = list(studies)
    papers = studies_to_papers(studies)
    papers = enrich_papers(
        papers,
        batch_size=settings.pubmed_batch_size,
        request_delay=settings.request_delay,
    )

    settings.papers_path.write_text(
        json.dumps([p.model_dump(by_alias=True) for p in papers], indent=2)
    )
    log.info("wrote %d papers -> %s", len(papers), settings.papers_path)

    return PipelineResult(
        studies_scanned=len(studies),
        papers_extracted=len(papers),
        papers_enriched=sum(1 for p in papers if p.journal_name or p.date),
        output_path=settings.papers_path,
    )
