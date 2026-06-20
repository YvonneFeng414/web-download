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

    # LLM model the browser-use agent drives (via OPENAI_API_KEY + BASE_URL).
    # A smaller model is the other big cost/latency lever (set WD_AGENT_MODEL).
    agent_model: str = Field(default="gpt-5.1")

    # Caps on the agent's run.
    agent_max_steps: int = Field(default=10, ge=1)
    agent_max_failures: int = Field(default=2, ge=0)

    # General task prompt template (formatted with the article {url}).
    prompt_path: Path = Field(default=Path("prompt-template/task_prompt.txt"))

    # --- Efficiency knobs (defaults tuned for cheaper/faster runs) ---
    # DOM-only by default (no per-step screenshot) — the biggest token/latency
    # win. Set WD_AGENT_USE_VISION=true for a page that needs screenshots.
    agent_use_vision: bool = Field(default=False)
    # browser-use's fast path: less per-step reasoning.
    agent_flash_mode: bool = Field(default=True)
    # More actions per LLM call -> fewer calls.
    agent_max_actions_per_step: int = Field(default=8, ge=1)
    # Cap conversation history growth -> fewer tokens per step.
    agent_max_history_items: int = Field(default=25, ge=1)
    # Abandon a stuck step sooner (browser-use default is 180s).
    agent_step_timeout: int = Field(default=90, ge=1)
    # Let the browser grab PDF links directly instead of via agent steps.
    auto_download_pdfs: bool = Field(default=True)
    # Page-load pacing, in seconds (lower = faster steps).
    wait_between_actions: float = Field(default=0.3, ge=0.0)
    network_idle_wait: float = Field(default=1.0, ge=0.0)
    min_page_load_wait: float = Field(default=0.25, ge=0.0)

    # --- Post-download verification ---
    # Confirm each downloaded PDF is actually a protocol/SAP; delete the rest.
    verify_downloads: bool = Field(default=True)
    # Model used for the content check (a smaller model is ideal/cheap here).
    verify_model: str = Field(default="gpt-5.1")
    # First pages of the PDF to read for the check.
    verify_pages: int = Field(default=10, ge=1)

    # Run the browser without a visible window. Defaults to False: publisher
    # anti-bot challenges (e.g. JAMA's Cloudflare) only clear in headed mode.
    headless: bool = Field(default=False)

    # Per-request navigation/network timeout in milliseconds.
    timeout_ms: int = Field(default=30_000, ge=1_000)

    # Persistent browser profile dir. Caches the Cloudflare clearance cookie
    # (cf_clearance) and fingerprint across runs so the challenge is solved once.
    user_data_dir: Path = Field(default=Path(".cf-profile"))

    def ensure_dirs(self) -> None:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """Return a freshly loaded ``Settings`` (re-reads env/.env each call)."""
    return Settings()
