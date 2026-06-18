"""Pydantic data models shared across pipeline stages."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DocType = Literal[
    "study protocol",
    "statistical analysis plan",
    "eMethods",
    "supplementary tables-figures",
    "consent form",
    "other",
]

# doc_types that count as "a protocol" worth downloading.
PROTOCOL_DOC_TYPES: frozenset[str] = frozenset(
    {"study protocol", "statistical analysis plan"}
)


class SupplementCandidate(BaseModel):
    """A candidate supplementary file discovered on an article page."""

    label: str = Field(description="Human-readable link text / caption.")
    url: str = Field(description="Absolute URL to the supplementary file.")
    file_type: str = Field(default="pdf", description="Lowercased file extension.")
    source: Literal["adapter", "agent"] = "adapter"


class ProtocolDecision(BaseModel):
    """Classifier verdict for a single supplement, skimmed from its first pages."""

    is_protocol: bool
    doc_type: DocType
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class SupplementResult(BaseModel):
    """A candidate plus its decision and (if kept) the saved path."""

    candidate: SupplementCandidate
    decision: ProtocolDecision | None = None
    downloaded_path: str | None = None
    error: str | None = None


class PipelineResult(BaseModel):
    article_url: str
    publisher: str
    results: list[SupplementResult] = Field(default_factory=list)

    @property
    def downloaded(self) -> list[SupplementResult]:
        return [r for r in self.results if r.downloaded_path]
