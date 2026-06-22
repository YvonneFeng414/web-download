"""Command-line entry point.

Three input modes (exactly one required):
- ``web-download <url>``           — download from an article URL
- ``web-download --title "..."``   — resolve a title -> DOI -> URL, then download
- ``web-download --papers PATH``   — batch over a search_papers ``papers.json``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

from web_download.config import get_settings
from web_download.models import PipelineResult
from web_download.pipeline import run, run_from_title, run_titles
from web_download.report import BookkeepingWriter


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="web-download",
        description="Identify and download protocol PDFs from an article page.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("url", nargs="?", help="Article URL")
    src.add_argument("--title", help="Paper title/citation to resolve via DOI")
    src.add_argument("--papers", help="Path to a papers.json to batch over")

    p.add_argument(
        "--start",
        type=int,
        default=0,
        help="0-based index of the first paper to process in --papers mode "
        "(skip the preceding ones; useful to resume a batch)",
    )
    p.add_argument("--limit", type=int, help="Max papers to process in --papers mode")
    p.add_argument("--out", help="Output directory for downloaded protocols")
    p.add_argument("--headed", action="store_true", help="Show the browser window")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def _load_titles(papers_path: str, start: int, limit: int | None) -> list[str]:
    data = json.loads(Path(papers_path).read_text())
    titles = [item["paper_title"] for item in data if item.get("paper_title")]
    titles = titles[start:] if start else titles
    return titles[:limit] if limit else titles


def _print_result(result: PipelineResult) -> None:
    if result.source_title is not None:
        print(f"\nTitle: {result.source_title[:70]!r}")
        print(f"DOI: {result.doi or '—'}  ->  URL: {result.article_url or '—'}")
    else:
        print(f"\nArticle: {result.article_url}")
    if result.error:
        print(f"  skipped: {result.error}")
        return
    if result.downloaded_paths:
        for path in result.downloaded_paths:
            print(f"  [SAVED] {path}")
    else:
        print("  no protocol downloaded")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    settings = get_settings()
    if args.out:
        settings.downloads_dir = Path(args.out)
    if args.headed:
        settings.headless = False

    # Append a bookkeeping row per paper as it completes (survives interruption).
    with BookkeepingWriter(settings.downloads_dir) as bk:
        if args.papers:
            titles = _load_titles(args.papers, args.start, args.limit)
            results = asyncio.run(run_titles(titles, settings, on_result=bk.write))
        elif args.title:
            results = [
                asyncio.run(run_from_title(args.title, settings, on_result=bk.write))
            ]
        else:
            results = [asyncio.run(run(args.url, settings, on_result=bk.write))]

    for result in results:
        _print_result(result)

    saved = sum(len(r.downloaded) for r in results)
    print(f"\nDownloaded {saved} protocol PDF(s) -> {settings.downloads_dir}")
    print(f"Bookkeeping CSV -> {bk.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
