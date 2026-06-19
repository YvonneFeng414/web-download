from web_download import resolve

CITATION = (
    "Franck LS, et al. Associations between Infant and Parent Characteristics ... "
    "J Pediatr. 2020 Jun;221:64-71.e4. doi: 10.1016/j.jpeds.2020.02.024."
)


def test_extract_doi_found():
    assert resolve.extract_doi(CITATION).startswith("10.1016/j.jpeds.2020.02.024")


def test_extract_doi_absent():
    assert resolve.extract_doi("A title with no identifier") is None


def test_resolve_returns_first_non_ncbi_link(monkeypatch):
    captured = {}

    def fake_search(query, api_key, cse_id, **kwargs):
        captured["query"] = query
        return {"items": [{"link": "https://publisher.example/article"}]}

    monkeypatch.setattr(resolve, "google_search", fake_search)
    url = resolve.resolve_article_url(CITATION, api_key="k", cse_id="c")
    assert url == "https://publisher.example/article"
    assert "10.1016/j.jpeds.2020.02.024" in captured["query"]
    assert "-site:pubmed.ncbi.nlm.nih.gov" in captured["query"]


def test_resolve_returns_none_on_no_items(monkeypatch):
    monkeypatch.setattr(resolve, "google_search", lambda *a, **k: {"items": []})
    assert resolve.resolve_article_url(CITATION, api_key="k", cse_id="c") is None


def test_resolve_skips_when_no_doi(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("google_search must not be called without a DOI")

    monkeypatch.setattr(resolve, "google_search", boom)
    assert resolve.resolve_article_url("no doi here") is None
