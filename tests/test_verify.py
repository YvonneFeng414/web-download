import re

import fitz  # PyMuPDF

from web_download import verify
from web_download.config import Settings
from web_download.verify import (
    ProtocolCheck,
    build_digest,
    decide,
    extract_first_pages_text,
    extract_pages,
    pdf_outline_titles,
    verify_and_filter,
)


def _make_pdf(path, *texts, toc=None):
    """Write a PDF, one page per text arg. Pass toc=[[level, title, page], ...]."""
    doc = fitz.open()
    for text in texts:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=9)
    if toc:
        doc.set_toc(toc)
    doc.save(str(path))
    doc.close()


def _check(is_protocol, confidence, doc_type="x", reason="r"):
    return ProtocolCheck(
        is_protocol=is_protocol, doc_type=doc_type, confidence=confidence, reason=reason
    )


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def test_extract_pages_returns_one_entry_per_page(tmp_path):
    pdf = tmp_path / "multi.pdf"
    _make_pdf(pdf, "page one", "page two", "page three")
    pages = extract_pages(pdf, 400)
    assert len(pages) == 3
    assert "page one" in pages[0] and "page three" in pages[2]


def test_extract_pages_respects_the_scan_cap(tmp_path):
    pdf = tmp_path / "capped.pdf"
    _make_pdf(pdf, "a", "b", "c")
    assert len(extract_pages(pdf, 2)) == 2


def test_extract_non_pdf_returns_empty(tmp_path):
    junk = tmp_path / "x.pdf"
    junk.write_bytes(b"not a pdf")
    assert extract_pages(junk, 3) == []
    assert extract_first_pages_text(junk, 3) == ""


def test_pdf_outline_titles(tmp_path):
    pdf = tmp_path / "toc.pdf"
    _make_pdf(pdf, "a", "b", toc=[[1, "Study Protocol", 2]])
    assert pdf_outline_titles(pdf) == ["Study Protocol"]
    assert pdf_outline_titles(tmp_path / "missing.pdf") == []


# --------------------------------------------------------------------------- #
# Digest — what the classifier actually gets to see
# --------------------------------------------------------------------------- #
def test_digest_carries_front_bookmarks_and_page_map():
    pages = ["Supplementary appendix\ncover", "eTable 1", "Study Protocol v3"]
    digest = build_digest(pages, ["Study Protocol"], front_pages=1)
    assert "Pages: 3" in digest
    assert "== Bookmarks ==" in digest and "- Study Protocol" in digest
    assert "== First pages (verbatim) ==" in digest
    assert "Supplementary appendix" in digest
    assert "p3: Study Protocol v3" in digest


def test_digest_page_map_reaches_a_protocol_buried_past_the_front():
    """The core requirement: a protocol deep inside a composite supplement must be
    visible to the classifier even though the front pages are all tables."""
    pages = [f"eTable {i}. Baseline characteristics" for i in range(60)]
    pages.append("Study Protocol\nProtocol Version 3.0\nInclusion criteria")
    digest = build_digest(pages, [], front_pages=10)

    assert "Study Protocol" not in digest.split("== Page map")[0]  # not in the front
    assert "p61: Study Protocol" in digest  # but the page map still shows it


def test_digest_samples_evenly_when_the_document_is_long():
    pages = [f"sheet {i}" for i in range(500)]
    digest = build_digest(pages, [], front_pages=2)
    rows = re.findall(r"^p(\d+): ", digest.split("== Page map")[1], re.MULTILINE)
    assert len(rows) <= verify._MAX_MAPPED_PAGES
    assert "sampled evenly" in digest
    # coverage spans the whole document, not just its start
    assert int(rows[0]) == 1
    assert int(rows[-1]) > 400


def test_digest_skips_blank_pages():
    digest = build_digest(["cover", "   ", "\n", "real content"], [], front_pages=1)
    assert "p2:" not in digest and "p3:" not in digest
    assert "p4: real content" in digest


# --------------------------------------------------------------------------- #
# decide() — the only thing standing between a verdict and an unlink
# --------------------------------------------------------------------------- #
def test_decide_positive_kept():
    keep, reason = decide(_check(True, 0.4), reject_threshold=0.7)
    assert keep is True and "kept: classifier" in reason


def test_decide_confident_negative_rejected():
    keep, reason = decide(_check(False, 0.9), reject_threshold=0.7)
    assert keep is False and "rejected (conf" in reason


def test_decide_low_confidence_negative_kept():
    keep, reason = decide(_check(False, 0.3), reject_threshold=0.7)
    assert keep is True and "low-confidence" in reason


def test_decide_failed_classification_is_kept():
    """classify_document returns confidence 0.0 on error; that must never delete."""
    failed = _check(False, 0.0, doc_type="unknown", reason="verify failed: boom")
    keep, _ = decide(failed, reject_threshold=0.7)
    assert keep is True


# --------------------------------------------------------------------------- #
# verify_and_filter()
# --------------------------------------------------------------------------- #
async def test_every_pdf_with_text_is_classified(tmp_path, monkeypatch):
    """No rule tier short-circuits the LLM any more — even an obvious protocol is
    sent for classification."""
    obvious = tmp_path / "protocol.pdf"
    _make_pdf(obvious, "Study Protocol\nProtocol Version 3.0\nInclusion criteria")

    seen = {}

    async def fake_classify(name, digest, **kwargs):
        seen["name"], seen["digest"] = name, digest
        return _check(True, 0.95, doc_type="study protocol"), 42

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(verify, "classify_document", fake_classify)

    kept, rejected, tokens = await verify_and_filter([str(obvious)], Settings())
    assert kept == [str(obvious)] and obvious.exists()
    assert rejected == [] and tokens == 42
    assert seen["name"] == "protocol.pdf"
    assert "Study Protocol" in seen["digest"]


async def test_confident_negative_deletes_the_file(tmp_path, monkeypatch):
    tables = tmp_path / "efigures.pdf"
    _make_pdf(tables, "eFigures and eTables only\nBaseline demographics")

    async def fake_classify(name, digest, **kwargs):
        return _check(False, 0.95, doc_type="tables", reason="supplementary tables"), 30

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(verify, "classify_document", fake_classify)

    kept, rejected, tokens = await verify_and_filter([str(tables)], Settings())
    assert kept == [] and not tables.exists()
    assert [p for p, _ in rejected] == [str(tables)] and tokens == 30


async def test_composite_protocol_is_visible_to_the_classifier(tmp_path, monkeypatch):
    """Regression: the protocol sits behind pages of tables, so the digest — not
    just the front matter — has to carry it to the LLM."""
    composite = tmp_path / "appendix.pdf"
    _make_pdf(
        composite,
        "eTable 1. Baseline characteristics",
        "eFigure 1. Kaplan-Meier curve",
        "Study Protocol\nStudy Objectives\nInclusion criteria\nPrimary Endpoint",
    )

    async def fake_classify(name, digest, **kwargs):
        assert "p3: Study Protocol" in digest, "buried protocol missing from digest"
        return _check(True, 0.9, doc_type="composite supplement"), 60

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(verify, "classify_document", fake_classify)

    kept, rejected, _ = await verify_and_filter([str(composite)], Settings())
    assert kept == [str(composite)] and composite.exists()
    assert rejected == []


async def test_keeps_no_text_pdf_without_calling_the_llm(tmp_path, monkeypatch):
    junk = tmp_path / "scan.pdf"
    junk.write_bytes(b"not a pdf")

    async def boom(*a, **k):
        raise AssertionError("classifier must not run on no-text files")

    monkeypatch.setattr(verify, "classify_document", boom)
    kept, rejected, tokens = await verify_and_filter([str(junk)], Settings())
    assert kept == [str(junk)] and rejected == [] and tokens == 0


async def test_keeps_non_pdf_without_calling_the_llm(tmp_path, monkeypatch):
    doc = tmp_path / "appendix.docx"
    doc.write_bytes(b"PK\x03\x04 some docx")

    async def boom(*a, **k):
        raise AssertionError("classifier must not run on non-PDFs")

    monkeypatch.setattr(verify, "classify_document", boom)
    kept, rejected, tokens = await verify_and_filter([str(doc)], Settings())
    assert kept == [str(doc)] and rejected == [] and tokens == 0
