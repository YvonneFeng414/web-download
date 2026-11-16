"""Step 4 - Select the "R1" analysis set: trial papers in top journals published after completion.

Filters (all configurable):
  1. lag > --min-lag-days, where
         lag = PubMed date - primary completion date   (in days)
     Both dates are truncated to the first of the month before subtracting,
     because ClinicalTrials.gov completion dates are often month-only ("2017-12").
  2. journal_name in --journals   (default: NEJM, Lancet, JAMA, BMJ)
  3. ref_type in --ref-types      (default: RESULT and DERIVED)
  4. optional: PubMed date >= --min-pub-date

Adds:
  diff_days : the lag above
  doi       : parsed from the citation string ("doi: 10.xxxx/...")

Also writes a per-journal paper count over the *unfiltered* input
(--journal-counts) to help decide which journals to include.

Usage:
    python 04_select_r1_papers.py
    python 04_select_r1_papers.py --journals JAMA --min-pub-date 2011-01-01 --out data/r1_jama.csv
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"

# Names exactly as PubMed `fulljournalname` reports them.
TOP_JOURNALS = [
    "The New England journal of medicine",
    "Lancet (London, England)",
    "JAMA",
    "BMJ (Clinical research ed.)",
]

OUTPUT_COLUMNS = [
    "nctId",
    "paper_title",
    "pmid",
    "ref_type",
    "primary_completion_date",
    "study_completion_date",
    "journal_name",
    "date",
    "diff_days",
    "doi",
]

_DOI_RE = re.compile(r"doi:\s*(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.IGNORECASE)


def doi_from_citation(citation: str) -> str | None:
    m = _DOI_RE.search(str(citation))
    return m.group(1).rstrip(".") if m else None


def to_month_start(s: pd.Series) -> pd.Series:
    """Parse mixed date strings ("2017-12", "2017-12-31", "2017/12/31 06:00") -> first of month."""
    return pd.to_datetime(s, format="mixed", errors="coerce").dt.to_period("M").dt.to_timestamp()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--papers", type=Path, default=DATA_DIR / "papers.json")
    p.add_argument("--out", type=Path, default=DATA_DIR / "r1_papers.csv")
    p.add_argument("--journal-counts", type=Path, default=DATA_DIR / "journal_counts.csv")
    p.add_argument("--journals", nargs="+", default=TOP_JOURNALS)
    p.add_argument("--ref-types", nargs="+", default=["RESULT", "DERIVED"])
    p.add_argument("--min-lag-days", type=int, default=0, help="Keep rows with diff_days strictly greater than this.")
    p.add_argument("--min-pub-date", default=None, help="e.g. 2011-01-01")
    args = p.parse_args()

    with open(args.papers, encoding="utf-8") as f:
        df = pd.DataFrame(json.load(f))
    print(f"Input papers: {len(df):,} rows, {df['nctId'].nunique():,} trials")

    args.journal_counts.parent.mkdir(parents=True, exist_ok=True)
    df["journal_name"].value_counts().rename_axis("journal_name").rename("count").to_csv(args.journal_counts)

    df["date"] = to_month_start(df["date"])
    df["primary_completion_date"] = to_month_start(df["primary_completion_date"])
    df["diff_days"] = (df["date"] - df["primary_completion_date"]).dt.days

    mask = (
        (df["diff_days"] > args.min_lag_days)
        & df["journal_name"].isin(args.journals)
        & df["ref_type"].isin(args.ref_types)
    )
    if args.min_pub_date:
        mask &= df["date"] >= pd.Timestamp(args.min_pub_date)
    r1 = df[mask].copy()

    r1["diff_days"] = r1["diff_days"].astype(int)
    r1["doi"] = r1["paper_title"].map(doi_from_citation)

    for col in ("date", "primary_completion_date"):
        r1[col] = r1[col].dt.strftime("%Y-%m-%d")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    r1[OUTPUT_COLUMNS].to_csv(args.out, index=False)

    print(f"R1 papers: {len(r1):,} rows, {r1['nctId'].nunique():,} trials -> {args.out}")
    print(r1["journal_name"].value_counts().to_string())
    print(f"Missing DOI: {r1['doi'].isna().sum():,}")


if __name__ == "__main__":
    main()
