import fitz  # PyMuPDF

from web_download import verify
from web_download.config import Settings
from web_download.verify import (
    ProtocolCheck,
    extract_first_pages_text,
    verify_and_filter,
    verify_pdf,
)


def _make_pdf(path, text):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def test_extract_first_pages_text(tmp_path):
    pdf = tmp_path / "p.pdf"
    _make_pdf(pdf, "Study Protocol: trial design and endpoints")
    assert "Study Protocol" in extract_first_pages_text(pdf, 3)


def test_extract_non_pdf_returns_empty(tmp_path):
    junk = tmp_path / "x.pdf"
    junk.write_bytes(b"not a pdf")
    assert extract_first_pages_text(junk, 3) == ""


class _FakeParse:
    def __init__(self, decision):
        self._decision = decision
        self.called = False

    async def parse(self, **kwargs):
        self.called = True
        from types import SimpleNamespace

        msg = SimpleNamespace(parsed=self._decision)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _FakeClient:
    def __init__(self, decision):
        self._parser = _FakeParse(decision)
        from types import SimpleNamespace

        self.chat = SimpleNamespace(completions=self._parser)


async def test_verify_pdf_maps_decision(tmp_path):
    pdf = tmp_path / "p.pdf"
    _make_pdf(pdf, "Statistical Analysis Plan ...")
    decision = ProtocolCheck(is_protocol=True, doc_type="SAP", reason="planned analyses")
    out = await verify_pdf(pdf, model="m", client=_FakeClient(decision), max_pages=3)
    assert out.is_protocol is True and out.doc_type == "SAP"


async def test_verify_pdf_empty_text_skips_llm(tmp_path):
    junk = tmp_path / "x.pdf"
    junk.write_bytes(b"not a pdf")
    client = _FakeClient(ProtocolCheck(is_protocol=True, doc_type="x", reason="y"))
    out = await verify_pdf(junk, model="m", client=client, max_pages=3)
    assert out.is_protocol is False
    assert client._parser.called is False  # no LLM call when there's no text


async def test_verify_and_filter_deletes_non_protocol(tmp_path, monkeypatch):
    keep = tmp_path / "protocol.pdf"
    drop = tmp_path / "figures.pdf"
    _make_pdf(keep, "Study Protocol")
    _make_pdf(drop, "eFigures")
    docx = tmp_path / "appendix.docx"
    docx.write_bytes(b"PK\x03\x04")  # non-PDF kept untouched

    async def fake_verify_pdf(path, **kwargs):
        is_p = "protocol" in path.name
        return ProtocolCheck(is_protocol=is_p, doc_type="x", reason="r")

    monkeypatch.setattr(verify, "verify_pdf", fake_verify_pdf)

    kept, rejected = await verify_and_filter(
        [str(keep), str(drop), str(docx)], Settings()
    )

    assert str(keep) in kept and str(docx) in kept
    assert [p for p, _ in rejected] == [str(drop)]
    assert keep.exists() and docx.exists()
    assert not drop.exists()  # deleted
