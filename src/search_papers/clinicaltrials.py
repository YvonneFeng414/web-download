"""Fetch studies from the ClinicalTrials.gov v2 API.

Pagination is ``pageToken``-chained and inherently sequential. Rather than
holding every study in memory, each page is written to its own JSON shard under
``ct_shards_dir`` and yielded to the caller as it arrives.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path

import requests

log = logging.getLogger("search_papers")

CLINICAL_TRIAL_STUDIES_URL = "https://clinicaltrials.gov/api/v2/studies"
SHARD_GLOB = "shard_*.json"


def _shard_path(ct_shards_dir: Path, page: int) -> Path:
    return ct_shards_dir / f"shard_{page:05d}.json"


def fetch_studies(
    ct_shards_dir: Path,
    *,
    overall_status: str,
    fields: str,
    page_size: int = 1000,
    max_results: int = 562_284,
    request_delay: float = 0.5,
    save_shards: bool = True,
) -> Iterator[dict]:
    """Page through the API, writing each page to a shard and yielding studies."""
    ct_shards_dir.mkdir(parents=True, exist_ok=True)

    params = {
        "pageSize": page_size,
        "countTotal": "true",
        "filter.overallStatus": overall_status,
        "fields": fields,
    }
    log.info("ClinicalTrials.gov query params: %s", params)

    fetched = 0
    page_count = 0
    page_token: str | None = None
    session = requests.Session()

    while fetched < max_results:
        if page_token:
            params["pageToken"] = page_token

        try:
            response = session.get(
                CLINICAL_TRIAL_STUDIES_URL, params=params, timeout=10
            )
            data = response.json()
        except requests.exceptions.RequestException as e:
            log.error("error on page %d: %s", page_count, e)
            break

        studies = data.get("studies", [])
        page_count += 1
        fetched += len(studies)
        log.info("page %d, fetched %d/%d", page_count, fetched, max_results)

        if save_shards and studies:
            _shard_path(ct_shards_dir, page_count).write_text(
                json.dumps(studies, indent=2)
            )

        yield from studies

        page_token = data.get("nextPageToken")
        if not page_token:
            log.info("no more pages")
            break

        time.sleep(request_delay)


def load_shards(ct_shards_dir: Path) -> Iterator[dict]:
    """Yield studies from previously saved shards under ``ct_shards_dir``."""
    shards = sorted(ct_shards_dir.glob(SHARD_GLOB))
    if not shards:
        log.warning("no shards found in %s", ct_shards_dir)
    for shard in shards:
        studies = json.loads(shard.read_text())
        yield from studies
