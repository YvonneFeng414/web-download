from web_download import resolve

CITATION = (
    "Franck LS, et al. Associations between Infant and Parent Characteristics ... "
    "J Pediatr. 2020 Jun;221:64-71.e4. doi: 10.1016/j.jpeds.2020.02.024."
)


def test_extract_doi_found():
    # trailing citation period is stripped
    assert resolve.extract_doi(CITATION) == "10.1016/j.jpeds.2020.02.024"


def test_extract_doi_absent():
    assert resolve.extract_doi("A title with no identifier") is None


def test_resolve_returns_doi_org_url():
    url = resolve.resolve_article_url(CITATION)
    assert url == "https://doi.org/10.1016/j.jpeds.2020.02.024"


def test_resolve_skips_when_no_doi():
    assert resolve.resolve_article_url("no doi here") is None
