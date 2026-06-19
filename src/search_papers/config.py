"""Runtime settings for the search-papers pipeline.

Values can be overridden via environment variables (prefixed ``SP_``) or a
``.env`` file in the project root.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default ClinicalTrials.gov ``fields`` projection. ``NCTId`` returns the study
# identification module; the other modules feed reference/date/contact parsing.
DEFAULT_FIELDS = (
    "NCTId,protocolSection.referencesModule,protocolSection.statusModule,"
    "protocolSection.contactsLocationsModule"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Where per-page ClinicalTrials.gov study shards are written/read.
    ct_shards_dir: Path = Field(default=Path("downloads/ct-gov"))

    # Final enriched papers output.
    papers_path: Path = Field(default=Path("downloads/papers.json"))

    # ClinicalTrials.gov pagination.
    page_size: int = Field(default=1000, ge=1, le=1000)
    max_results: int = Field(default=562_284, ge=1)
    overall_status: str = Field(default="COMPLETED")
    fields: str = Field(default=DEFAULT_FIELDS)

    # Politeness delay between paged API requests, in seconds.
    request_delay: float = Field(default=0.5, ge=0.0)

    # Number of pmids per PubMed esummary request.
    pubmed_batch_size: int = Field(default=200, ge=1)

    def ensure_dirs(self) -> None:
        self.ct_shards_dir.mkdir(parents=True, exist_ok=True)
        self.papers_path.parent.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """Return a freshly loaded ``Settings`` (re-reads env/.env each call)."""
    return Settings()
