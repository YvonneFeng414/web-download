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

    # Caps on the agent's run. max_failures matches browser-use's own default:
    # a lower cap aborts a run on two transient provider hiccups in a row.
    agent_max_steps: int = Field(default=15, ge=1)
    agent_max_failures: int = Field(default=5, ge=0)

    # Optional second provider. browser-use switches to it for the rest of a run
    # when the primary returns a retryable error (401/402/429/5xx) — including the
    # malformed structured output a non-compliant proxy produces. Built only when
    # WD_FALLBACK_BASE_URL is set; the other two default to the primary's values.
    fallback_base_url: str | None = Field(default=None)
    fallback_api_key: str | None = Field(default=None)
    fallback_model: str | None = Field(default=None)

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
    agent_use_vision: bool = Field(default=False)
    # browser-use's fast path: less per-step reasoning.
    agent_flash_mode: bool = Field(default=True)
    # More actions per LLM call -> fewer calls.
    agent_max_actions_per_step: int = Field(default=8, ge=1)
    # Cap conversation history growth -> fewer tokens per step.
    agent_max_history_items: int = Field(default=25, ge=1)
    # Per-LLM-call budget. browser-use would otherwise infer 75s from the model
    # name, which a slower proxy overshoots.
    agent_llm_timeout: int = Field(default=120, ge=1)
    # Ceiling on a single completion. Raised from browser-use's 4096 because
    # reasoning tokens are billed against it: any model whose name contains
    # "gpt-5" (or o1/o3/o4) is sent reasoning_effort, and a step whose reasoning
    # eats the budget returns finish_reason="length" with an EMPTY content —
    # which the parser can only report as a step failure.
    agent_max_completion_tokens: int = Field(default=16_384, ge=1)
    # Abandon a stuck step sooner (browser-use default is 180s). Must stay ABOVE
    # agent_llm_timeout, else the step aborts before the LLM call can finish.
    agent_step_timeout: int = Field(default=150, ge=1)
    # browser-use's post-run LLM judge. Off: it costs an extra call carrying the
    # whole trace, and verify_downloads below is the check we actually act on.
    agent_use_judge: bool = Field(default=False)
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
    # Model used for the content check. Every verified file costs one call, so
    # this is the whole verify budget — a smaller model is the lever to pull.
    verify_model: str = Field(default="gpt-5.4")
    # Front pages included VERBATIM in the digest sent to the classifier (the
    # cover page / table of contents, where a protocol names itself). The rest of
    # the document still reaches the LLM as the digest's page map.
    verify_pages: int = Field(default=10, ge=1)
    # Min classifier confidence-in-a-negative needed to DELETE a file. Higher =
    # more conservative; anything softer is kept.
    verify_reject_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    # Safety cap: never read more than this many pages of a pathological PDF.
    verify_max_scan_pages: int = Field(default=400, ge=1)
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

    # Reuse ONE browser for every article in a run instead of launching a fresh
    # one per article. Required for a manual sign-in to hold: publisher SSO
    # (Shibboleth/SAML) issues session-scoped cookies that the profile above
    # cannot cache — they die with the browser process. Also solves Cloudflare
    # once per run rather than per article. Set WD_SHARE_BROWSER=false to go back
    # to per-article browsers, which isolates a crash to a single article.
    share_browser: bool = Field(default=True)

    def ensure_dirs(self) -> None:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """Return a freshly loaded ``Settings`` (re-reads env/.env each call)."""
    return Settings()
