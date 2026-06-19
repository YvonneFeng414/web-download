import csv

from web_download.models import PipelineResult
from web_download.report import BookkeepingWriter, CSV_NAME, write_bookkeeping


def _downloaded_result():
    return PipelineResult(
        article_url="https://jamanetwork.com/a/1",
        source_title="Paper A doi: 10.1/x",
        doi="10.1/x",
        downloaded_paths=["downloads/10.1_x/protocol.pdf"],
    )


def test_write_bookkeeping_rows(tmp_path):
    results = [
        _downloaded_result(),
        # downloaded a file, but verification rejected/deleted it
        PipelineResult(
            article_url="https://x/2",
            source_title="B",
            rejected_paths=["downloads/x/figs.pdf"],
        ),
        # skipped before any work (no DOI)
        PipelineResult(article_url="", source_title="C", error="no DOI"),
    ]

    path = write_bookkeeping(results, tmp_path)
    assert path == tmp_path / CSV_NAME

    rows = list(csv.DictReader(path.open()))
    assert [r["status"] for r in rows] == ["protocol", "no_protocol", "skipped"]

    protocol = rows[0]
    assert protocol["doi"] == "10.1/x"
    assert protocol["n_downloaded"] == "1"
    assert protocol["downloaded_paths"].endswith("protocol.pdf")
    # rejected download is reflected, not counted as a protocol
    assert rows[1]["n_rejected"] == "1"
    assert "rejected" in rows[1]["note"]
    assert rows[2]["note"] == "no DOI"


def test_bookkeeping_writer_appends_incrementally(tmp_path):
    with BookkeepingWriter(tmp_path) as bk:
        assert bk.path.read_text().strip() == "title,doi,url,status,n_downloaded,n_rejected,downloaded_paths,note"

        bk.write(_downloaded_result())
        assert len(bk.path.read_text().strip().splitlines()) == 2

        bk.write(PipelineResult(article_url="", error="no DOI"))
        assert len(bk.path.read_text().strip().splitlines()) == 3

    rows = list(csv.DictReader(bk.path.open()))
    assert [r["status"] for r in rows] == ["protocol", "skipped"]


def test_write_bookkeeping_creates_out_dir(tmp_path):
    out = tmp_path / "nested" / "out"
    path = write_bookkeeping([], out)
    assert path.exists()
    assert path.read_text().strip().startswith("title,doi,url")
