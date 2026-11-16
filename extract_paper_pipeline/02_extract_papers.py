"""Step 2 - Flatten study records into one record per linked publication.

For every study from step 1, keeps each reference that
  - has both a PMID and a citation string, and
  - has a reference type in --ref-types (default: RESULT, DERIVED).

  RESULT  = publication reporting the trial's results (added by the sponsor)
  DERIVED = publication matched to the trial by NLM via the NCT number
  (BACKGROUND references are background literature and are dropped.)

Output: a JSON list (same layout as papers.json, minus the PubMed fields that
step 3 adds), one object per (trial, paper):
  nctId, paper_title (full citation string), pmid, ref_type,
  primary_completion_date, study_completion_date

Usage:
    python 02_extract_papers.py
    python 02_extract_papers.py --studies data/studies_sample.jsonl --out data/papers_raw_sample.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterator

from tqdm import tqdm

DATA_DIR = Path(__file__).resolve().parent / "data"


def iter_studies(path: Path) -> Iterator[dict]:
    """Yield studies from a .jsonl file (step 1 output) or a legacy studies.json list."""
    if path.suffix == ".jsonl":
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
    else:
        with open(path, encoding="utf-8") as f:
            yield from json.load(f)


def study_to_papers(study: dict, ref_types: set[str]) -> list[dict]:
    proto = study.get("protocolSection", {})
    refs = proto.get("referencesModule", {}).get("references", [])
    if not refs:
        return []

    nct_id = proto.get("identificationModule", {}).get("nctId")
    status = proto.get("statusModule", {})
    primary_completion = status.get("primaryCompletionDateStruct", {}).get("date")
    study_completion = status.get("completionDateStruct", {}).get("date")

    papers = []
    for ref in refs:
        if "citation" not in ref or "pmid" not in ref:
            continue
        if ref.get("type") not in ref_types:
            continue
        papers.append(
            {
                "nctId": nct_id,
                "paper_title": ref["citation"],
                "pmid": ref["pmid"],
                "ref_type": ref["type"],
                "primary_completion_date": primary_completion,
                "study_completion_date": study_completion,
            }
        )
    return papers


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--studies", type=Path, default=DATA_DIR / "studies.jsonl")
    p.add_argument("--out", type=Path, default=DATA_DIR / "papers_raw.json")
    p.add_argument("--ref-types", nargs="+", default=["RESULT", "DERIVED"])
    args = p.parse_args()

    ref_types = set(args.ref_types)
    papers: list[dict] = []
    n_studies = n_with_papers = 0
    for study in tqdm(iter_studies(args.studies), desc="studies", unit="study"):
        n_studies += 1
        rows = study_to_papers(study, ref_types)
        if rows:
            n_with_papers += 1
            papers.extend(rows)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(papers, f, indent=2)

    print(
        f"Studies read: {n_studies:,} | with >=1 paper: {n_with_papers:,} | "
        f"papers: {len(papers):,} -> {args.out}"
    )


if __name__ == "__main__":
    main()
