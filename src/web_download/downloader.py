"""Download a protocol PDF with a single browser-use Agent.

The task prompt (shared body + the one publisher fragment, see
``web_download.publishers``) tells the agent to open the article, find the
supplementary-material section, and download the study protocol / statistical
analysis plan. Files land directly in ``dest_dir`` because the browser-use
``Browser`` is created with ``downloads_path=dest_dir``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from web_download.config import Settings
from web_download.publishers import build_task_prompt

log = logging.getLogger("web_download")


def _files_in(directory: Path) -> set[Path]:
    """Regular (non-hidden) files directly in ``directory``."""
    if not directory.exists():
        return set()
    return {p for p in directory.iterdir() if p.is_file() and not p.name.startswith(".")}


def _usage(history) -> tuple[float, int]:
    """Pull (cost_usd, tokens) out of a browser-use history, defaulting to 0."""
    u = getattr(history, "usage", None)
    if u is None:
        return 0.0, 0
    return float(getattr(u, "total_cost", 0.0) or 0.0), int(
        getattr(u, "total_tokens", 0) or 0
    )


async def download_article(
    url: str,
    dest_dir: Path,
    settings: Settings,
    *,
    publisher: str | None = None,
) -> tuple[list[str], str | None, float, int]:
    """Run the agent against ``url``.

    ``publisher`` picks which publisher fragment goes into the task prompt; when
    omitted it is detected from the URL/DOI.

    Returns ``(downloaded paths, error|None, cost_usd, tokens)``. Any exception is
    captured and returned as the error string so a batch run is never aborted by
    one bad article.
    """
    from browser_use import Agent, Browser
    from browser_use.llm import ChatOpenAI

    dest_dir.mkdir(parents=True, exist_ok=True)

    browser = Browser(
        headless=settings.headless,
        user_data_dir=str(settings.user_data_dir),
        accept_downloads=True,
        downloads_path=str(dest_dir.resolve()),
        auto_download_pdfs=settings.auto_download_pdfs,
        wait_between_actions=settings.wait_between_actions,
        wait_for_network_idle_page_load_time=settings.network_idle_wait,
        minimum_wait_page_load_time=settings.min_page_load_wait,
    )
    llm = ChatOpenAI(
        model=settings.agent_model,
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("BASE_URL"),
    )

    error: str | None = None
    cost_usd, tokens = 0.0, 0
    try:
        agent = Agent(
            task=build_task_prompt(url, settings, publisher=publisher),
            llm=llm,
            browser=browser,
            calculate_cost=True,
            max_failures=settings.agent_max_failures,
            use_vision=settings.agent_use_vision,
            flash_mode=settings.agent_flash_mode,
            max_actions_per_step=settings.agent_max_actions_per_step,
            max_history_items=settings.agent_max_history_items,
            step_timeout=settings.agent_step_timeout,
        )
        history = await agent.run(max_steps=settings.agent_max_steps)
        cost_usd, tokens = _usage(history)
        log.info("agent usage for %s: $%.4f, %d tokens", url, cost_usd, tokens)
    except Exception as exc:  # network, agent, or browser failure
        error = f"agent failed: {exc}"
        log.warning("%s", error)
    finally:
        try:
            await browser.kill()
        except Exception:
            pass

    # Verify every file now in this per-DOI folder, not just files that are new
    # since the run started. A re-run overwrites an existing download in place,
    # which a before/after diff would miss — leaving a real protocol unverified.
    downloaded = sorted(str(p) for p in _files_in(dest_dir))
    return downloaded, error, cost_usd, tokens
