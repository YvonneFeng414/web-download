"""End-to-end orchestrator.

Flow: resolve a title to an article URL (or take a URL directly) -> run a
browser-use agent that downloads the study protocol / SAP into a per-DOI
subdirectory -> record the outcome.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from urllib.parse import urlparse

from dotenv import load_dotenv

from web_download.config import Settings, get_settings
from web_download.downloader import download_article
from web_download.models import PipelineResult
from web_download.resolve import extract_doi, resolve_article_url
from web_download.verify import verify_and_filter

log = logging.getLogger("web_download")

# Called once per paper as it completes (e.g. to append a bookkeeping row).
ResultHook = Callable[[PipelineResult], None] | None

_SLUG = re.compile(r"[^A-Za-z0-9._-]+")

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


async def _run_one(
    url: str,
    settings: Settings,
    *,
    source_title: str | None = None,
    doi: str | None = None,
) -> PipelineResult:
    dest_dir = _dest_subdir(settings, doi, url)
    paths, error = await download_article(url, dest_dir, settings)

    rejected_paths: list[str] = []
    if not error and paths and settings.verify_downloads:
        try:
            paths, rejected = await verify_and_filter(paths, settings)
            rejected_paths = [p for p, _ in rejected]
        except Exception as exc:  # never let verification abort the run
            log.warning("verification failed for %s: %s", url, exc)

    return PipelineResult(
        article_url=url,
        source_title=source_title,
        doi=doi,
        downloaded_paths=paths,
        rejected_paths=rejected_paths,
        error=error,
    )


async def run(
    url: str,
    settings: Settings | None = None,
    *,
    on_result: ResultHook = None,
) -> PipelineResult:
    """Download the protocol for a single article URL."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    result = await _run_one(url, settings, doi=extract_doi(url))
    if on_result is not None:
        on_result(result)
    return result


async def run_titles(
    titles: list[str],
    settings: Settings | None = None,
    *,
    on_result: ResultHook = None,
) -> list[PipelineResult]:
    """Resolve each title to a URL and download its protocol.

    Titles without a resolvable DOI/URL yield a skipped ``PipelineResult``.
    ``on_result`` is invoked once per paper as it completes (e.g. to append a
    bookkeeping row incrementally).
    """
    settings = settings or get_settings()
    settings.ensure_dirs()

    results: list[PipelineResult] = []
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
            result = await _run_one(url, settings, source_title=title, doi=doi)

        results.append(result)
        if on_result is not None:
            on_result(result)

    return results


async def run_from_title(
    title: str,
    settings: Settings | None = None,
    *,
    on_result: ResultHook = None,
) -> PipelineResult:
    """Resolve a single title to a URL and download its protocol."""
    results = await run_titles([title], settings, on_result=on_result)
    return results[0]
