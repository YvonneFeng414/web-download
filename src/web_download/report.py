"""Per-paper bookkeeping CSV summarizing protocol-download outcomes.

One row per processed paper, recording whether a protocol was found/downloaded,
skipped (no DOI/URL), or processed without any protocol. Written into the output
directory so a batch run leaves an auditable ledger alongside the PDFs.
"""

from __future__ import annotations

import csv
from pathlib import Path

from web_download.models import PipelineResult

CSV_NAME = "bookkeeping.csv"
_FIELDS = [
    "title",
    "doi",
    "url",
    "status",
    "n_downloaded",
    "n_rejected",
    "downloaded_paths",
    "note",
]


def _status(result: PipelineResult) -> str:
    """One of ``skipped`` (no DOI/URL), ``protocol`` (verified), ``no_protocol``."""
    if result.error:
        return "skipped"
    if result.downloaded_paths:
        return "protocol"
    return "no_protocol"


def _note(result: PipelineResult) -> str:
    if result.error:
        return result.error
    if result.rejected_paths:
        return f"{len(result.rejected_paths)} file(s) rejected (not a protocol/SAP)"
    return ""


def _row(result: PipelineResult) -> dict[str, object]:
    paths = result.downloaded_paths
    return {
        "title": result.source_title or "",
        "doi": result.doi or "",
        "url": result.article_url or "",
        "status": _status(result),
        "n_downloaded": len(paths),
        "n_rejected": len(result.rejected_paths),
        "downloaded_paths": "; ".join(paths),
        "note": _note(result),
    }


class BookkeepingWriter:
    """Append bookkeeping rows one at a time as each paper completes.

    Opening writes the header immediately and every ``write`` flushes, so a long
    batch run leaves an up-to-date ledger even if it is interrupted partway. Use
    as a context manager::

        with BookkeepingWriter(out_dir) as bk:
            bk.write(result)
    """

    def __init__(self, out_dir: Path):
        self.path = out_dir / CSV_NAME
        self._fh = None
        self._writer: csv.DictWriter | None = None

    def __enter__(self) -> "BookkeepingWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=_FIELDS)
        self._writer.writeheader()
        self._fh.flush()
        return self

    def write(self, result: PipelineResult) -> None:
        assert self._writer is not None and self._fh is not None, "writer not open"
        self._writer.writerow(_row(result))
        self._fh.flush()

    def __exit__(self, *exc) -> None:
        if self._fh is not None:
            self._fh.close()


def write_bookkeeping(results: list[PipelineResult], out_dir: Path) -> Path:
    """Write one CSV row per result into ``out_dir/bookkeeping.csv``; return path."""
    with BookkeepingWriter(out_dir) as bk:
        for result in results:
            bk.write(result)
    return bk.path
