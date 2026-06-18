"""Runtime settings for the pipeline.

Values can be overridden via environment variables (prefixed ``WD_``) or a
``.env`` file in the project root. ``OPENAI_API_KEY`` is read by the OpenAI SDK
directly and is not duplicated here.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Where downloaded protocol PDFs are saved.
    downloads_dir: Path = Field(default=Path("downloads"))

    # Number of leading PDF pages to extract for the protocol decision.
    skim_pages: int = Field(default=3, ge=1)

    # Minimum classifier confidence required to keep a PDF.
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)

    # OpenAI model used for the protocol classification step.
    classifier_model: str = Field(default="gpt-4o-mini")

    # OpenAI model used by the browser-use fallback agent.
    agent_model: str = Field(default="gpt-4o")

    # Run the browser without a visible window. Defaults to False: publisher
    # anti-bot challenges (e.g. JAMA's Cloudflare) only clear in headed mode.
    headless: bool = Field(default=False)

    # Per-request navigation/network timeout in milliseconds.
    timeout_ms: int = Field(default=30_000, ge=1_000)

    def ensure_dirs(self) -> None:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """Return a freshly loaded ``Settings`` (re-reads env/.env each call)."""
    return Settings()
