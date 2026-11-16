"""Download a protocol PDF with a single browser-use Agent.

The task prompt (shared body + the one publisher fragment, see
``web_download.publishers``) tells the agent to open the article, find the
supplementary-material section, and download the study protocol / statistical
analysis plan.

Two browser lifetimes are supported:

- **Per-article** (``browser=None``): a fresh ``Browser`` is created and killed
  around the one run, with ``downloads_path=dest_dir``, so files land straight in
  the per-DOI folder.
- **Shared** (``browser`` from :func:`build_shared_browser`): one browser serves
  every article in a run. This is the only way a manual sign-in survives past the
  first article — publisher SSO (Shibboleth/SAML) issues session-scoped cookies
  that die with the browser process, so no amount of profile reuse brings them
  back. A live browser cannot retarget its download folder (browser-use sends
  ``Browser.setDownloadBehavior`` once per session), so every article downloads
  into one ``staging_dir`` and :func:`_drain_staging` sorts each run's files into
  the per-DOI folder afterwards.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import NamedTuple

from web_download.config import Settings
from web_download.llm import build_llms
from web_download.publishers import build_task_prompt

log = logging.getLogger("web_download")

# Chrome's in-progress download suffixes. Never sort a half-written file into a
# per-DOI folder, or the verifier reads a truncated PDF.
_PARTIAL_SUFFIXES = (".crdownload", ".part", ".tmp")


class DownloadOutcome(NamedTuple):
    """What one agent run left behind.

    ``paths`` is every file in the per-DOI folder — the verifier must see files a
    re-run overwrote in place, which a before/after set-diff would miss.
    ``preexisting`` is the subset that this run did not touch, so a failed run is
    not credited with a download left by an earlier one.
    """

    paths: list[str]
    preexisting: list[str]
    error: str | None
    cost_usd: float
    tokens: int


def _files_in(directory: Path) -> set[Path]:
    """Regular (non-hidden) files directly in ``directory``."""
    if not directory.exists():
        return set()
    return {p for p in directory.iterdir() if p.is_file() and not p.name.startswith(".")}


def _mtimes(directory: Path) -> dict[Path, int]:
    """Modification time per file, for spotting what this run actually wrote."""
    stamps: dict[Path, int] = {}
    for path in _files_in(directory):
        try:
            stamps[path] = path.stat().st_mtime_ns
        except OSError:  # vanished mid-scan
            continue
    return stamps


def _browser_kwargs(settings: Settings, downloads_path: Path) -> dict:
    """Browser construction shared by both lifetimes."""
    return {
        "headless": settings.headless,
        "user_data_dir": str(settings.user_data_dir),
        "accept_downloads": True,
        "downloads_path": str(downloads_path.resolve()),
        "auto_download_pdfs": settings.auto_download_pdfs,
        "wait_between_actions": settings.wait_between_actions,
        "wait_for_network_idle_page_load_time": settings.network_idle_wait,
        "minimum_wait_page_load_time": settings.min_page_load_wait,
    }


def build_shared_browser(settings: Settings, staging_dir: Path):
    """A browser to reuse for every article in a run.

    ``keep_alive`` is what stops browser-use from closing it at the end of each
    ``agent.run()``, which is what lets one sign-in cover the whole batch. The
    caller owns the lifetime and must ``kill()`` it when the run is done.
    """
    from browser_use import Browser

    staging_dir.mkdir(parents=True, exist_ok=True)
    return Browser(keep_alive=True, **_browser_kwargs(settings, staging_dir))


def _drain_staging(
    staging_dir: Path, before: dict[Path, int], dest_dir: Path
) -> set[Path]:
    """Move what this run downloaded out of ``staging_dir`` into ``dest_dir``.

    Returns the destination paths written. A file unchanged since ``before`` was
    left by an earlier article (its move failed) and is skipped, so one bad move
    does not get re-credited to every later article.
    """
    moved: set[Path] = set()
    for path, stamp in _mtimes(staging_dir).items():
        if before.get(path) == stamp or path.name.endswith(_PARTIAL_SUFFIXES):
            continue
        target = dest_dir / path.name
        try:
            shutil.move(str(path), str(target))
        except OSError as exc:  # keep the batch going; the file stays in staging
            log.warning("could not move %s into %s: %s", path.name, dest_dir, exc)
            continue
        moved.add(target)
    return moved


def _forget_tracked_downloads(browser) -> None:
    """Drop a shared session's record of what earlier articles downloaded.

    One ``BrowserSession`` serves the whole batch, and browser-use mirrors its
    download ledger into every new agent's ``available_file_paths`` — so without
    this, article N starts already "holding" article N-1's staging path, which
    :func:`_drain_staging` has since emptied. The task prompt treats that list as
    the proof a download happened, making a stale entry a standing false success.
    It also un-sticks tracking: the session refuses to re-record a path it has
    already seen, and every Lancet supplement is ``mmc1.pdf`` arriving at the same
    staging path. Private attribute, so a rename degrades to the old behaviour
    rather than killing the batch.
    """
    ledger = getattr(browser, "_downloaded_files", None)
    if isinstance(ledger, list):
        ledger.clear()


def _usage(history) -> tuple[float, int]:
    """Pull (cost_usd, tokens) out of a browser-use history, defaulting to 0."""
    u = getattr(history, "usage", None)
    if u is None:
        return 0.0, 0
    return float(getattr(u, "total_cost", 0.0) or 0.0), int(
        getattr(u, "total_tokens", 0) or 0
    )


def _history_error(history) -> str | None:
    """Why the agent stopped, for a run that ended without raising.

    ``agent.run()`` does not raise when the agent gives up — it folds "stopped due
    to N consecutive failures" into the history — so without this a dead run looks
    like a clean one.
    """
    is_successful = getattr(history, "is_successful", None)
    if not callable(is_successful):
        return None
    outcome = is_successful()
    if outcome is True:
        return None

    errors = [e for e in (history.errors() or []) if e]
    detail = f": {errors[-1].splitlines()[0]}" if errors else ""
    if outcome is False:
        return f"agent reported failure{detail}"
    # None => never reached a `done` action: out of steps, or too many failures.
    return f"agent stopped without finishing{detail}"


async def download_article(
    url: str,
    dest_dir: Path,
    settings: Settings,
    *,
    publisher: str | None = None,
    browser=None,
    staging_dir: Path | None = None,
) -> DownloadOutcome:
    """Run the agent against ``url``.

    ``publisher`` picks which publisher fragment goes into the task prompt; when
    omitted it is detected from the URL/DOI.

    Pass ``browser`` (with its ``staging_dir``) to reuse a caller-owned browser so
    a sign-in carries across articles; it is left running afterwards. Omit both to
    get a private browser that downloads straight into ``dest_dir`` and is killed
    at the end of the run.

    Any exception is captured into ``DownloadOutcome.error`` so a batch run is
    never aborted by one bad article.
    """
    from browser_use import Agent

    dest_dir.mkdir(parents=True, exist_ok=True)

    # Where this run's downloads will show up, sampled before the agent starts so
    # its writes can be told apart from what an earlier run left.
    shared = browser is not None
    if shared:
        if staging_dir is None:
            raise ValueError("staging_dir is required when reusing a browser")
        before = _mtimes(staging_dir)
        # Every agent starts with an empty ledger, so it can neither inherit an
        # earlier article's download as proof of its own nor be denied credit for
        # re-downloading a name a previous article already used.
        _forget_tracked_downloads(browser)
    else:
        from browser_use import Browser

        before = _mtimes(dest_dir)
        browser = Browser(**_browser_kwargs(settings, dest_dir))
    llm, fallback_llm = build_llms(settings)

    error: str | None = None
    cost_usd, tokens = 0.0, 0
    try:
        agent = Agent(
            task=build_task_prompt(url, settings, publisher=publisher),
            llm=llm,
            fallback_llm=fallback_llm,
            browser=browser,
            calculate_cost=True,
            max_failures=settings.agent_max_failures,
            use_vision=settings.agent_use_vision,
            use_judge=settings.agent_use_judge,
            flash_mode=settings.agent_flash_mode,
            max_actions_per_step=settings.agent_max_actions_per_step,
            max_history_items=settings.agent_max_history_items,
            llm_timeout=settings.agent_llm_timeout,
            step_timeout=settings.agent_step_timeout,
        )
        history = await agent.run(max_steps=settings.agent_max_steps)
        cost_usd, tokens = _usage(history)
        log.info("agent usage for %s: $%.4f, %d tokens", url, cost_usd, tokens)
        error = _history_error(history)
    except Exception as exc:  # network, agent, or browser failure
        error = f"agent failed: {exc}"
        log.warning("%s", error)
    finally:
        if not shared:  # a shared browser outlives this run; its owner kills it
            try:
                await browser.kill()
            except Exception:
                pass

    if shared:
        fresh = _drain_staging(staging_dir, before, dest_dir)
    else:
        fresh = {p for p, stamp in _mtimes(dest_dir).items() if before.get(p) != stamp}

    after = _mtimes(dest_dir)
    preexisting = sorted(str(p) for p in after if p not in fresh)

    if fresh:
        # The agent saved something, so a noisy trace is not a failed run.
        error = None
    elif error is None:
        # done(success=True) with nothing on disk. The agent cannot see whether a
        # click actually saved a file, and a publisher that serves supplements
        # inline (NEJM) gives an ordinary navigation for its "Download" link — so
        # a confident agent reports downloads it never made. Only the filesystem
        # settles it, and disagreeing with the agent here is the whole point.
        error = "agent reported success but no file reached disk"
        log.warning("%s: %s", url, error)
    elif preexisting:
        log.info(
            "%s: %s — %d pre-existing file(s) left from an earlier run",
            url,
            error,
            len(preexisting),
        )

    return DownloadOutcome(
        paths=sorted(str(p) for p in after),
        preexisting=preexisting,
        error=error,
        cost_usd=cost_usd,
        tokens=tokens,
    )
