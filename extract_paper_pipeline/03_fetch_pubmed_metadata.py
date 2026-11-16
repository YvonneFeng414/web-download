"""Step 3 - Add journal name and PubMed date to each paper -> papers.json.

Queries NCBI E-utilities esummary for each unique PMID (in batches) and adds
two keys to every record from step 2:
  journal_name : PubMed `fulljournalname` (e.g. "The New England journal of medicine")
  date         : when the record entered PubMed (history pubstatus == "pubmed"),
                 as PubMed reports it, e.g. "2022/11/18 06:00"
Both are null when PubMed has no value (or the lookup failed).

The result is the final papers.json:
  [{"nctId", "paper_title", "pmid", "ref_type", "primary_completion_date",
    "study_completion_date", "journal_name", "date"}, ...]

NCBI rate limits: 3 requests/s without an API key, 10/s with one. Set the
NCBI_API_KEY environment variable to use a key (optional).

Usage:
    python 03_fetch_pubmed_metadata.py
    python 03_fetch_pubmed_metadata.py --papers data/papers_raw_sample.json --out data/papers_sample.json
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import requests
from tqdm import tqdm

ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
DATA_DIR = Path(__file__).resolve().parent / "data"
MAX_RETRIES = 5


def parse_summary(res: dict) -> dict:
    date = None
    for stage in res.get("history", []):
        if stage.get("pubstatus") == "pubmed":
            date = stage.get("date")
    return {"journal_name": res.get("fulljournalname"), "date": date}


def fetch_batch(session: requests.Session, pmids: list[str], api_key: str | None) -> dict[str, dict]:
    """Return {pmid: {journal_name, date}} for one batch. POST avoids URL-length limits."""
    data = {"db": "pubmed", "id": ",".join(pmids), "retmode": "json"}
    if api_key:
        data["api_key"] = api_key

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(ESUMMARY_URL, data=data, timeout=60)
            resp.raise_for_status()
            result = resp.json()["result"]
            break
        except (requests.RequestException, ValueError, KeyError) as e:
            if attempt == MAX_RETRIES:
                tqdm.write(f"[skip] batch starting {pmids[0]} failed after {MAX_RETRIES} tries: {e}")
                return {}
            wait = 2**attempt
            tqdm.write(f"[retry {attempt}/{MAX_RETRIES}] {e} - sleeping {wait}s")
            time.sleep(wait)

    return {uid: parse_summary(result.get(uid, {})) for uid in result.get("uids", [])}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--papers", type=Path, default=DATA_DIR / "papers_raw.json")
    p.add_argument("--out", type=Path, default=DATA_DIR / "papers.json")
    p.add_argument("--batch-size", type=int, default=200)
    args = p.parse_args()

    api_key = os.getenv("NCBI_API_KEY")
    sleep = 0.11 if api_key else 0.34

    with open(args.papers, encoding="utf-8") as f:
        papers = json.load(f)

    pmids = list(dict.fromkeys(str(x["pmid"]) for x in papers))  # unique, order kept
    print(f"Papers: {len(papers):,} | unique PMIDs: {len(pmids):,} | api_key: {'yes' if api_key else 'no'}")

    session = requests.Session()
    meta: dict[str, dict] = {}
    for i in tqdm(range(0, len(pmids), args.batch_size), desc="pubmed batches"):
        meta.update(fetch_batch(session, pmids[i : i + args.batch_size], api_key))
        time.sleep(sleep)

    empty = {"journal_name": None, "date": None}
    for item in papers:
        item.update(meta.get(str(item["pmid"]), empty))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(papers, f, indent=2)

    n_missing = sum(1 for x in papers if x["journal_name"] is None)
    print(f"Wrote {len(papers):,} papers -> {args.out} ({n_missing:,} without a PubMed journal name)")


if __name__ == "__main__":
    main()
