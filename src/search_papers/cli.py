"""Command-line entry point: ``search-papers [options]``."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

from search_papers.config import get_settings
from search_papers.pipeline import run


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="search-papers",
        description=(
            "Fetch ClinicalTrials.gov studies, extract their reference papers, "
            "and enrich them with PubMed journal/date metadata."
        ),
    )
    p.add_argument("--out", help="Output path for the enriched papers JSON")
    p.add_argument("--shards-dir", help="Directory for ClinicalTrials.gov shards")
    p.add_argument("--status", help="filter.overallStatus value (e.g. COMPLETED)")
    p.add_argument("--max-results", type=int, help="Cap on studies fetched")
    p.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Reuse existing shards instead of calling ClinicalTrials.gov",
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
        settings.papers_path = Path(args.out)
    if args.shards_dir:
        settings.ct_shards_dir = Path(args.shards_dir)
    if args.status:
        settings.overall_status = args.status
    if args.max_results is not None:
        settings.max_results = args.max_results

    result = run(settings, skip_fetch=args.skip_fetch)

    print(f"\nStudies scanned:  {result.studies_scanned}")
    print(f"Papers extracted: {result.papers_extracted}")
    print(f"Papers enriched:  {result.papers_enriched}")
    print(f"Output:           {result.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
