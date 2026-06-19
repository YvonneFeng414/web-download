"""Flatten ClinicalTrials.gov studies into reference ``Paper`` rows."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from search_papers.models import Paper

log = logging.getLogger("search_papers")

# Reference types that point at an actual publication for the study.
KEPT_REF_TYPES = {"DERIVED", "RESULT"}


def studies_to_papers(studies: Iterable[dict]) -> list[Paper]:
    """Extract one ``Paper`` per qualifying reference across all studies."""
    papers: list[Paper] = []

    for s in studies:
        protocol = s.get("protocolSection", {})
        references_mod = protocol.get("referencesModule", {})
        references = references_mod.get("references")
        if not references:
            continue

        nct_id = protocol.get("identificationModule", {}).get("nctId")
        if not nct_id:
            continue

        status_mod = protocol.get("statusModule", {})
        primary_completion_date = status_mod.get(
            "primaryCompletionDateStruct", {}
        ).get("date")
        study_completion_date = status_mod.get("completionDateStruct", {}).get(
            "date"
        )

        for r in references:
            if "citation" not in r or "pmid" not in r:
                continue
            ref_type = r.get("type")
            if ref_type not in KEPT_REF_TYPES:
                continue

            papers.append(
                Paper(
                    nct_id=nct_id,
                    paper_title=r["citation"],
                    pmid=str(r["pmid"]),
                    ref_type=ref_type,
                    primary_completion_date=primary_completion_date,
                    study_completion_date=study_completion_date,
                )
            )

    log.info("extracted %d papers", len(papers))
    return papers
