"""Command-line entry point: ``web-download <url> [--out DIR]``."""

from __future__ import annotations

import argparse
import asyncio
import logging

from dotenv import load_dotenv

from web_download.config import get_settings
from web_download.pipeline import run


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="web-download",
        description="Identify and download protocol PDFs from an article page.",
    )
    p.add_argument("url", help="Article URL")
    p.add_argument("--out", help="Output directory for downloaded protocols")
    p.add_argument("--skim-pages", type=int, help="Pages to read per PDF")
    p.add_argument(
        "--threshold", type=float, help="Min confidence to keep (0..1)"
    )
    p.add_argument(
        "--headed", action="store_true", help="Show the browser window"
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    settings = get_settings()
    if args.out:
        settings.downloads_dir = settings.downloads_dir.__class__(args.out)
    if args.skim_pages:
        settings.skim_pages = args.skim_pages
    if args.threshold is not None:
        settings.confidence_threshold = args.threshold
    if args.headed:
        settings.headless = False

    result = asyncio.run(run(args.url, settings))

    print(f"\nArticle: {result.article_url}")
    print(f"Publisher: {result.publisher}")
    print(f"Candidates: {len(result.results)}")
    for r in result.results:
        d = r.decision
        verdict = (
            f"{d.doc_type} (protocol={d.is_protocol}, conf={d.confidence:.2f})"
            if d
            else (r.error or "—")
        )
        mark = "[SAVED]" if r.downloaded_path else "       "
        print(f"  {mark} {r.candidate.label[:60]!r}: {verdict}")
    print(f"\nDownloaded {len(result.downloaded)} protocol PDF(s) -> {settings.downloads_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
