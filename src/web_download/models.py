"""Pydantic data models shared across pipeline stages."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PipelineResult(BaseModel):
    """Outcome of attempting to download a protocol for one article."""

    article_url: str
    source_title: str | None = None
    doi: str | None = None
    downloaded_paths: list[str] = Field(default_factory=list)
    rejected_paths: list[str] = Field(default_factory=list)
    error: str | None = None

    # Cost summary: download agent cost (USD) + tokens, and verify-step tokens/cost.
    download_cost: float = 0.0
    download_tokens: int = 0
    verify_tokens: int = 0
    verify_cost: float = 0.0

    @property
    def downloaded(self) -> list[str]:
        return self.downloaded_paths
