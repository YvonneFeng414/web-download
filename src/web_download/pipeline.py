"""End-to-end orchestrator.

Flow: resolve a title to an article URL (or take a URL directly) -> run a
browser-use agent that downloads the study protocol / SAP into a per-DOI
subdirectory -> record the outcome.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from dotenv import load_dotenv

from web_download.config import Settings, get_settings
from web_download.downloader import build_shared_browser, download_article
from web_download.models import PipelineResult
from web_download.publishers import detect_publisher
from web_download.resolve import extract_doi, resolve_article_url
from web_download.verify import verify_and_filter

log = logging.getLogger("web_download")

# Called once per paper as it completes (e.g. to append a bookkeeping row).
ResultHook = Callable[[PipelineResult], None] | None

_SLUG = re.compile(r"[^A-Za-z0-9._-]+")

# Where a shared browser drops every article's downloads before they are sorted
# into per-DOI folders. Hidden so it never looks like a result directory.
_STAGING_DIRNAME = ".staging"

load_dotenv()


def _doi_slug(doi: str) -> str:
    """Turn a DOI into a single safe path segment.

    e.g. ``10.1016/j.jpeds.2020.02.024`` -> ``10.1016_j.jpeds.2020.02.024``.
    """
    return _SLUG.sub("_", doi.strip()).strip("._-") or "doi"


def _url_slug(url: str) -> str:
    """Fallback subdir name for a URL with no DOI: host + last path segment."""
    parsed = urlparse(url)
    tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    raw = f"{parsed.netloc}_{tail}" if tail else parsed.netloc
    return _SLUG.sub("_", raw).strip("._-") or "article"


def _dest_subdir(settings: Settings, doi: str | None, url: str):
    return settings.downloads_dir / (_doi_slug(doi) if doi else _url_slug(url))


async def _wait_for_login(browser, login_url: str | None) -> None:
    """Open the browser and block until the user has signed in by hand.

    Institutional SSO is interactive (password + MFA) and its session cookie is
    session-scoped, so the sign-in has to happen here: once, inside the very
    browser process the rest of the run reuses. Deliberately manual — credentials
    never reach the agent's prompt.
    """
    await browser.start()
    if login_url:
        await browser.navigate_to(login_url)
    print("\nA browser window is open. Sign in to the publisher / your institution.")
    print("Every article below is then downloaded in that same signed-in session.")
    # input() blocks the event loop; off-thread so the browser stays responsive.
    await asyncio.to_thread(input, "Press Enter once you are signed in... ")


@asynccontextmanager
async def _batch_browser(
    settings: Settings, *, pause_for_login: bool, login_url: str | None
):
    """Yield ``(browser, staging_dir)`` reused across the run, or ``(None, None)``.

    ``(None, None)`` means each article gets its own browser — isolated, but no
    sign-in and no Cloudflare clearance carries between articles.
    """
    if not settings.share_browser:
        yield None, None
        return

    staging_dir = settings.downloads_dir / _STAGING_DIRNAME
    browser = build_shared_browser(settings, staging_dir)
    try:
        if pause_for_login or login_url:
            await _wait_for_login(browser, login_url)
        yield browser, staging_dir
    finally:
        try:
            await browser.kill()
        except Exception:  # never let teardown mask a run's results
            pass


async def _run_one(
    url: str,
    settings: Settings,
    *,
    source_title: str | None = None,
    doi: str | None = None,
    browser=None,
    staging_dir=None,
) -> PipelineResult:
    dest_dir = _dest_subdir(settings, doi, url)
    # --publisher / WD_PUBLISHER wins; otherwise infer it here, where the DOI is
    # still at hand (batch URLs are bare doi.org links with no publisher host).
    publisher = settings.publisher or detect_publisher(url, doi)
    log.info("using %s publisher notes for %s", publisher or "generic", url)
    outcome = await download_article(
        url,
        dest_dir,
        settings,
        publisher=publisher,
        browser=browser,
        staging_dir=staging_dir,
    )
    paths = outcome.paths

    rejected_paths: list[str] = []
    verify_tokens = 0
    # Verify whatever is on disk even when the agent errored: a file left by an
    # earlier run is still a real candidate, and the run is flagged separately via
    # ``error``/``preexisting_paths``.
    if paths and settings.verify_downloads:
        try:
            paths, rejected, verify_tokens = await verify_and_filter(paths, settings)
            rejected_paths = [p for p, _ in rejected]
        except Exception as exc:  # never let verification abort the run
            log.warning("verification failed for %s: %s", url, exc)

    verify_cost = verify_tokens / 1_000_000 * settings.verify_cost_per_mtok

    return PipelineResult(
        article_url=url,
        source_title=source_title,
        doi=doi,
        downloaded_paths=paths,
        # Narrowed to survivors: a pre-existing file the verifier rejected is gone.
        preexisting_paths=[p for p in outcome.preexisting if p in set(paths)],
        rejected_paths=rejected_paths,
        error=outcome.error,
        download_cost=outcome.cost_usd,
        download_tokens=outcome.tokens,
        verify_tokens=verify_tokens,
        verify_cost=verify_cost,
    )


async def run(
    url: str,
    settings: Settings | None = None,
    *,
    on_result: ResultHook = None,
    pause_for_login: bool = False,
    login_url: str | None = None,
) -> PipelineResult:
    """Download the protocol for a single article URL."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    async with _batch_browser(
        settings, pause_for_login=pause_for_login, login_url=login_url
    ) as (browser, staging_dir):
        result = await _run_one(
            url,
            settings,
            doi=extract_doi(url),
            browser=browser,
            staging_dir=staging_dir,
        )
    if on_result is not None:
        on_result(result)
    return result


async def run_titles(
    titles: list[str],
    settings: Settings | None = None,
    *,
    on_result: ResultHook = None,
    pause_for_login: bool = False,
    login_url: str | None = None,
) -> list[PipelineResult]:
    """Resolve each title to a URL and download its protocol.

    Titles without a resolvable DOI/URL yield a skipped ``PipelineResult``.
    ``on_result`` is invoked once per paper as it completes (e.g. to append a
    bookkeeping row incrementally).

    Unless ``share_browser`` is off, every paper runs in one browser, so a
    sign-in done at ``pause_for_login`` covers the whole list.
    """
    settings = settings or get_settings()
    settings.ensure_dirs()

    results: list[PipelineResult] = []
    async with _batch_browser(
        settings, pause_for_login=pause_for_login, login_url=login_url
    ) as (browser, staging_dir):
        for title in titles:
            doi = extract_doi(title)
            url = resolve_article_url(title)
            if url is None:
                result = PipelineResult(
                    article_url="",
                    source_title=title,
                    doi=doi,
                    error="no DOI" if doi is None else "no URL resolved",
                )
            else:
                log.info("resolved %.60s -> %s", title, url)
                result = await _run_one(
                    url,
                    settings,
                    source_title=title,
                    doi=doi,
                    browser=browser,
                    staging_dir=staging_dir,
                )

            results.append(result)
            if on_result is not None:
                on_result(result)

    return results


async def run_from_title(
    title: str,
    settings: Settings | None = None,
    *,
    on_result: ResultHook = None,
    pause_for_login: bool = False,
    login_url: str | None = None,
) -> PipelineResult:
    """Resolve a single title to a URL and download its protocol."""
    results = await run_titles(
        [title],
        settings,
        on_result=on_result,
        pause_for_login=pause_for_login,
        login_url=login_url,
    )
    return results[0]
