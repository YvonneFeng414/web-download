import fitz  # PyMuPDF

from web_download import verify
from web_download.config import Settings
from web_download.verify import (
    FULL_PROTOCOL,
    NO_PROTOCOL,
    PARTIAL_PROTOCOL,
    ProtocolCheck,
    RuleSignals,
    decide,
    extract_all_text,
    extract_first_pages_text,
    has_protocol_header,
    has_protocol_marker,
    has_protocol_metadata,
    pdf_outline_titles,
    score_protocol,
    section_score,
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


def _signals(
    has_header=False,
    section_score=0,
    has_metadata=False,
    classification=NO_PROTOCOL,
    confidence=0.0,
):
    return RuleSignals(
        has_header=has_header,
        section_score=section_score,
        has_metadata=has_metadata,
        classification=classification,
        confidence=confidence,
    )


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def test_extract_first_pages_text(tmp_path):
    pdf = tmp_path / "p.pdf"
    _make_pdf(pdf, "Study Protocol: trial design and endpoints")
    assert "Study Protocol" in extract_first_pages_text(pdf, 3)


def test_extract_non_pdf_returns_empty(tmp_path):
    junk = tmp_path / "x.pdf"
    junk.write_bytes(b"not a pdf")
    assert extract_first_pages_text(junk, 3) == ""


def test_extract_all_text_reads_every_page(tmp_path):
    pdf = tmp_path / "multi.pdf"
    _make_pdf(pdf, "front tables page", "middle figures page", "deep protocol page")
    text = extract_all_text(pdf, 400)
    assert "front tables" in text and "deep protocol" in text
    # a first-pages read capped at 1 page must NOT see the deep page
    assert "deep protocol" not in extract_first_pages_text(pdf, 1)


def test_pdf_outline_titles(tmp_path):
    pdf = tmp_path / "toc.pdf"
    _make_pdf(pdf, "a", "b", toc=[[1, "Study Protocol", 2]])
    assert pdf_outline_titles(pdf) == ["Study Protocol"]
    assert pdf_outline_titles(tmp_path / "missing.pdf") == []


# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #
def test_has_protocol_marker():
    assert has_protocol_marker("STUDY PROTOCOL\nVersion 3.0\n...")
    assert has_protocol_marker("Supplement 2.\nStatistical Analysis Plan (SAP)")
    # bare "protocol" deep in body text is not a marker
    assert not has_protocol_marker("eFigure 1. Outcomes by per-protocol analysis set")
    assert not has_protocol_marker("Supplementary tables and figures")


def test_has_protocol_header_whole_doc():
    # heading deep in the doc (past the 1,500-char cover region)
    deep = "eTable 1. Baseline\n" * 200 + "\nStudy Protocol\n"
    assert has_protocol_header(deep)
    # amendment/version line
    assert has_protocol_header("BeiGene Protocol Amendment 2.0  03 April 2020")
    assert has_protocol_header("Protocol Version 3")
    # PDF bookmark match even with no marker in the text
    assert has_protocol_header("eTables only", ["Statistical Analysis Plan"])
    # plain tables file is not a header
    assert not has_protocol_header("Supplementary eFigures and eTables")


def test_section_score_counts_distinct_categories():
    text = (
        "Study Objectives ... Primary Endpoint ... Study Design and Randomization "
        "... Inclusion criteria ... Exclusion criteria ... Statistical analysis ..."
    )
    # objectives, endpoints, design, eligibility, statistics = 5 distinct
    assert section_score(text) == 5
    assert section_score("eFigures and eTables only") == 0


def test_has_protocol_metadata():
    assert has_protocol_metadata("Protocol Number: BGB-A317-305")
    assert has_protocol_metadata("Registered as NCT01234567")
    assert has_protocol_metadata("A Phase 3 randomized, double-blind study")
    assert not has_protocol_metadata("Supplementary tables")


def test_score_protocol_full():
    text = (
        "BeiGene Protocol Amendment 2.0 (BGB-A317-305)\n"
        "Study Objectives\nPrimary Endpoint\nStudy Design, Randomization\n"
        "Inclusion criteria\nExclusion criteria\nStatistical analysis"
    )
    sig = score_protocol(text)
    assert sig.has_header and sig.has_metadata
    assert sig.section_score >= 4
    assert sig.classification == FULL_PROTOCOL


def test_score_protocol_partial():
    # enough sections, but no self-identifying header and no metadata block
    text = "Primary endpoint\nInclusion criteria\nStudy design"
    sig = score_protocol(text)
    assert not sig.has_header
    assert sig.section_score >= 3
    assert sig.classification == PARTIAL_PROTOCOL


def test_score_protocol_no_protocol():
    sig = score_protocol("eFigures and eTables only")
    assert sig.classification == NO_PROTOCOL
    assert sig.section_score == 0


# --------------------------------------------------------------------------- #
# decide()
# --------------------------------------------------------------------------- #
def test_decide_header_keeps_without_llm():
    keep, reason = decide(_signals(has_header=True), None, reject_threshold=0.7)
    assert keep is True and "header" in reason


def test_decide_full_classification_keeps_without_llm():
    sig = _signals(section_score=5, classification=FULL_PROTOCOL, confidence=0.9)
    keep, reason = decide(sig, None, reject_threshold=0.7)
    assert keep is True and FULL_PROTOCOL in reason


def test_decide_rules_reject_when_no_llm():
    keep, reason = decide(_signals(), None, reject_threshold=0.7)
    assert keep is False and "rejected by rules" in reason


def test_decide_llm_positive_kept():
    keep, _ = decide(_signals(), _check(True, 0.4), reject_threshold=0.7)
    assert keep is True


def test_decide_llm_confident_negative_rejected():
    keep, reason = decide(_signals(), _check(False, 0.9), reject_threshold=0.7)
    assert keep is False and "rejected (conf" in reason


def test_decide_llm_low_confidence_negative_kept():
    keep, reason = decide(_signals(), _check(False, 0.3), reject_threshold=0.7)
    assert keep is True and "low-confidence" in reason


# --------------------------------------------------------------------------- #
# verify_and_filter()
# --------------------------------------------------------------------------- #
async def test_composite_protocol_kept_despite_llm_negative(tmp_path, monkeypatch):
    """The core bug: protocol buried behind tables must be kept, and the front-only
    LLM (which sees tables) must not be able to delete it."""
    composite = tmp_path / "appendix.pdf"
    _make_pdf(
        composite,
        "eTable 1. Baseline characteristics",
        "eFigure 1. Kaplan-Meier curve",
        "Study Protocol\nStudy Objectives\nInclusion criteria\nPrimary Endpoint",
    )

    async def boom(*a, **k):
        raise AssertionError("Tier-1 keep must not call the LLM")

    monkeypatch.setattr(verify, "classify_text", boom)

    kept, rejected, tokens = await verify_and_filter([str(composite)], Settings())
    assert kept == [str(composite)] and composite.exists()
    assert rejected == [] and tokens == 0


async def test_rules_delete_plain_tables_without_llm(tmp_path, monkeypatch):
    """Tier 2: a plainly protocol-free file is deleted by rules, no LLM call."""
    tables = tmp_path / "efigures.pdf"
    _make_pdf(tables, "eFigures and eTables only\nBaseline demographics")

    async def boom(*a, **k):
        raise AssertionError("Tier-2 delete must not call the LLM")

    monkeypatch.setattr(verify, "classify_text", boom)

    kept, rejected, tokens = await verify_and_filter([str(tables)], Settings())
    assert kept == [] and not tables.exists()
    assert [p for p, _ in rejected] == [str(tables)] and tokens == 0


async def test_borderline_file_uses_llm(tmp_path, monkeypatch):
    """Tier 3: some sections but no header/metadata -> LLM arbitrates and can save it."""
    borderline = tmp_path / "border.pdf"
    # exactly 2 sections (endpoints + eligibility): above clear-negative, below partial
    _make_pdf(borderline, "Primary endpoint was survival.\nInclusion criteria: adults.")

    async def fake_classify(name, text, **kwargs):
        return _check(True, 0.8, doc_type="protocol"), 55

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")  # lazy client is built here
    monkeypatch.setattr(verify, "classify_text", fake_classify)

    kept, rejected, tokens = await verify_and_filter([str(borderline)], Settings())
    assert kept == [str(borderline)] and borderline.exists()
    assert rejected == [] and tokens == 55


async def test_keeps_no_text_pdf(tmp_path, monkeypatch):
    junk = tmp_path / "scan.pdf"
    junk.write_bytes(b"not a pdf")

    async def boom(*a, **k):
        raise AssertionError("classifier must not run on no-text files")

    monkeypatch.setattr(verify, "classify_text", boom)
    kept, rejected, tokens = await verify_and_filter([str(junk)], Settings())
    assert kept == [str(junk)] and rejected == [] and tokens == 0
