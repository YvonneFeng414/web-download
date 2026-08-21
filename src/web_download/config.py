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
    agent_model: str = Field(default="gpt-5.4")

    # Caps on the agent's run.
    agent_max_steps: int = Field(default=15, ge=1)
    agent_max_failures: int = Field(default=2, ge=0)

    # Shared task-prompt body (formatted with the article {url} and the chosen
    # publisher's {publisher_notes}).
    prompt_path: Path = Field(default=Path("prompt-template/task_prompt.txt"))
    # One <key>.txt fragment per publisher (jama, nejm, lancet, bmj) plus
    # generic.txt; only the matching one is injected into the agent's context.
    publisher_notes_dir: Path = Field(default=Path("prompt-template/publishers"))
    # Force a publisher instead of detecting it from the URL/DOI (WD_PUBLISHER or
    # --publisher). None = auto-detect, falling back to the generic notes.
    publisher: str | None = Field(default=None)

    # --- Efficiency knobs (defaults tuned for cheaper/faster runs) ---
    # DOM-only by default (no per-step screenshot) — the biggest token/latency
    # win. Set WD_AGENT_USE_VISION=true for a page that needs screenshots.
    agent_use_vision: bool = Field(default=True)
    # browser-use's fast path: less per-step reasoning.
    agent_flash_mode: bool = Field(default=True)
    # More actions per LLM call -> fewer calls.
    agent_max_actions_per_step: int = Field(default=8, ge=1)
    # Cap conversation history growth -> fewer tokens per step.
    agent_max_history_items: int = Field(default=25, ge=1)
    # Abandon a stuck step sooner (browser-use default is 180s).
    agent_step_timeout: int = Field(default=90, ge=1)
    # Off so the agent's explicit download is the only save (browser-use tracks
    # it), avoiding a parallel auto-save that creates "name (1).pdf" duplicates.
    # Set WD_AUTO_DOWNLOAD_PDFS=true for a rare viewer-only-PDF site.
    auto_download_pdfs: bool = Field(default=False)
    # Page-load pacing, in seconds (lower = faster steps).
    wait_between_actions: float = Field(default=0.3, ge=0.0)
    network_idle_wait: float = Field(default=1.0, ge=0.0)
    min_page_load_wait: float = Field(default=0.25, ge=0.0)

    # --- Post-download verification ---
    # Confirm each downloaded PDF is actually a protocol/SAP; delete the rest.
    verify_downloads: bool = Field(default=True)
    # Model used for the content check (a smaller model is ideal/cheap here).
    verify_model: str = Field(default="gpt-5.4")
    # First pages of the PDF to read for the LLM slice (Tier-3 borderline check).
    verify_pages: int = Field(default=10, ge=1)
    # Min classifier confidence-in-a-negative needed to DELETE a file (when the
    # document has no explicit protocol/SAP marker). Higher = more conservative.
    verify_reject_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    # Scan the WHOLE document (not just the first pages) with rule-based signals so
    # a protocol buried deep in a composite supplement is still found. Off => legacy
    # first-`verify_pages` behavior only (escape hatch).
    verify_whole_doc: bool = Field(default=True)
    # Safety cap: never scan more than this many pages of a pathological PDF.
    verify_max_scan_pages: int = Field(default=400, ge=1)
    # Distinct SPIRIT-style sections required to call a file a FULL protocol.
    verify_section_score_full: int = Field(default=4, ge=1, le=6)
    # ...and to call it a PARTIAL protocol (kept, but weaker signal).
    verify_section_score_partial: int = Field(default=3, ge=1, le=6)
    # At/below this section count, with no header and no metadata, the rules alone
    # may DELETE the file (Tier 2) without consulting the LLM.
    verify_section_score_clear_negative: int = Field(default=1, ge=0, le=6)
    # Price of the verify model, USD per 1,000,000 tokens, used to turn
    # verify_tokens into verify_cost_usd in the bookkeeping CSV.
    verify_cost_per_mtok: float = Field(default=0.0, ge=0.0)

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
