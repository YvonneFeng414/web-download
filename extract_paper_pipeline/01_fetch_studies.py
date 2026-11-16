"""Step 1 - Download trial records from the ClinicalTrials.gov v2 API.

Pages through https://clinicaltrials.gov/api/v2/studies and writes one study
per line (JSON Lines). Writing line by line keeps memory flat and means a
partial run still leaves usable output.

Only the modules needed downstream are requested:
  - NCTId                          -> protocolSection.identificationModule.nctId
  - protocolSection.statusModule   -> primary / study completion dates
  - protocolSection.referencesModule -> linked publications (PMID, type, citation)

Usage:
    python 01_fetch_studies.py
    python 01_fetch_studies.py --max-studies 500 --out data/studies_sample.jsonl
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests
from tqdm import tqdm

API_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).resolve().parent / "data"

DEFAULT_FIELDS = ",".join(
    [
        "NCTId",
        "protocolSection.statusModule",
        "protocolSection.referencesModule",
    ]
)

MAX_RETRIES = 5


def get_page(session: requests.Session, params: dict) -> dict:
    """GET one page, retrying on network errors, 429 and 5xx with backoff."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(API_URL, params=params, timeout=60)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == MAX_RETRIES:
                raise
            wait = 2**attempt
            tqdm.write(f"[retry {attempt}/{MAX_RETRIES}] {e} - sleeping {wait}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def fetch_studies(
    out_path: Path,
    status: str | None,
    fields: str,
    page_size: int,
    max_studies: int | None,
    sleep: float,
) -> int:
    params: dict = {"pageSize": page_size, "countTotal": "true", "fields": fields}
    if status:
        params["filter.overallStatus"] = status

    out_path.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    n_written = 0
    pbar = None

    with open(out_path, "w", encoding="utf-8") as f:
        while True:
            data = get_page(session, params)

            if pbar is None:
                total = data.get("totalCount")
                if max_studies is not None and total is not None:
                    total = min(total, max_studies)
                pbar = tqdm(total=total, desc="studies", unit="study")

            studies = data.get("studies", [])
            if max_studies is not None:
                studies = studies[: max_studies - n_written]

            for study in studies:
                f.write(json.dumps(study, ensure_ascii=False) + "\n")
            n_written += len(studies)
            pbar.update(len(studies))

            token = data.get("nextPageToken")
            if not token or (max_studies is not None and n_written >= max_studies):
                break
            params["pageToken"] = token
            time.sleep(sleep)

    if pbar is not None:
        pbar.close()
    return n_written


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=DATA_DIR / "studies.jsonl")
    p.add_argument(
        "--status",
        default="COMPLETED",
        help="filter.overallStatus value (comma-separated allowed). Use '' for all statuses.",
    )
    p.add_argument("--fields", default=DEFAULT_FIELDS, help="Comma-separated API field list.")
    p.add_argument("--page-size", type=int, default=1000, help="Max 1000.")
    p.add_argument("--max-studies", type=int, default=None, help="Stop after N studies (for testing).")
    p.add_argument("--sleep", type=float, default=0.5, help="Seconds between page requests.")
    args = p.parse_args()

    n = fetch_studies(
        out_path=args.out,
        status=args.status or None,
        fields=args.fields,
        page_size=args.page_size,
        max_studies=args.max_studies,
        sleep=args.sleep,
    )
    print(f"Wrote {n:,} studies -> {args.out}")


if __name__ == "__main__":
    main()
