"""Pydantic data models shared across the search-papers pipeline stages."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RefType = Literal["DERIVED", "RESULT"]


class Paper(BaseModel):
    """A reference paper extracted from a ClinicalTrials.gov study.

    The JSON keys are kept compatible with the legacy ``papers.json`` schema via
    field aliases (``nctId``/``paper_title``/``pmid``). Dump with
    ``model_dump(by_alias=True)`` to reproduce that exact shape.
    """

    model_config = ConfigDict(populate_by_name=True)

    nct_id: str = Field(alias="nctId")
    paper_title: str
    pmid: str
    ref_type: RefType
    primary_completion_date: str | None = None
    study_completion_date: str | None = None

    # Enrichment from the PubMed esummary API.
    journal_name: str | None = None
    date: str | None = None


class PipelineResult(BaseModel):
    """Summary of one end-to-end run."""

    studies_scanned: int = 0
    papers_extracted: int = 0
    papers_enriched: int = 0
    output_path: Path
