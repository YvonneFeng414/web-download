import fitz  # PyMuPDF

from web_download import verify
from web_download.config import Settings
from web_download.verify import (
    ProtocolCheck,
    decide,
    extract_first_pages_text,
    has_protocol_marker,
    verify_and_filter,
)


def _make_pdf(path, text):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def _check(is_protocol, confidence, doc_type="x", reason="r"):
    return ProtocolCheck(
        is_protocol=is_protocol, doc_type=doc_type, confidence=confidence, reason=reason
    )


def test_extract_first_pages_text(tmp_path):
    pdf = tmp_path / "p.pdf"
    _make_pdf(pdf, "Study Protocol: trial design and endpoints")
    assert "Study Protocol" in extract_first_pages_text(pdf, 3)


def test_extract_non_pdf_returns_empty(tmp_path):
    junk = tmp_path / "x.pdf"
    junk.write_bytes(b"not a pdf")
    assert extract_first_pages_text(junk, 3) == ""


def test_has_protocol_marker():
    assert has_protocol_marker("STUDY PROTOCOL\nVersion 3.0\n...")
    assert has_protocol_marker("Supplement 2.\nStatistical Analysis Plan (SAP)")
    # bare "protocol" deep in body text is not a marker
    assert not has_protocol_marker("eFigure 1. Outcomes by per-protocol analysis set")
    assert not has_protocol_marker("Supplementary tables and figures")


def test_decide_marker_overrides_negative():
    # explicit marker -> keep even if classifier is a confident negative
    keep, reason = decide(_check(False, 0.95), has_marker=True, reject_threshold=0.7)
    assert keep is True and "marker" in reason


def test_decide_classifier_positive_kept():
    keep, _ = decide(_check(True, 0.4), has_marker=False, reject_threshold=0.7)
    assert keep is True


def test_decide_confident_negative_rejected():
    keep, reason = decide(_check(False, 0.9), has_marker=False, reject_threshold=0.7)
    assert keep is False and "rejected" in reason


def test_decide_low_confidence_negative_kept():
    # "low confidence we should reject it" -> keep
    keep, reason = decide(_check(False, 0.3), has_marker=False, reject_threshold=0.7)
    assert keep is True and "low-confidence" in reason


async def test_verify_and_filter_keeps_marked_file_despite_llm_no(tmp_path, monkeypatch):
    marked = tmp_path / "protocol.pdf"
    unrelated = tmp_path / "figures.pdf"
    _make_pdf(marked, "Study Protocol\nFull trial design follows")
    _make_pdf(unrelated, "eFigures and eTables only")

    async def fake_classify(name, text, **kwargs):
        # LLM confidently says NOT a protocol for both
        return _check(False, 0.95)

    monkeypatch.setattr(verify, "classify_text", fake_classify)

    kept, rejected = await verify_and_filter([str(marked), str(unrelated)], Settings())

    # marked file survives (fact overrides), unrelated is deleted
    assert str(marked) in kept and marked.exists()
    assert [p for p, _ in rejected] == [str(unrelated)]
    assert not unrelated.exists()


async def test_verify_and_filter_keeps_no_text_pdf(tmp_path, monkeypatch):
    junk = tmp_path / "scan.pdf"
    junk.write_bytes(b"not a pdf")

    async def boom(*a, **k):
        raise AssertionError("classifier must not run on no-text files")

    monkeypatch.setattr(verify, "classify_text", boom)
    kept, rejected = await verify_and_filter([str(junk)], Settings())
    assert kept == [str(junk)] and rejected == []
