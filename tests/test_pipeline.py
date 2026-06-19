from web_download.downloader import load_prompt
from web_download.pipeline import _doi_slug, _url_slug


def test_doi_slug():
    assert _doi_slug("10.1016/j.jpeds.2020.02.024") == "10.1016_j.jpeds.2020.02.024"
    assert _doi_slug("10.1001/jama.2016.1.") == "10.1001_jama.2016.1"
    assert _doi_slug("///") == "doi"


def test_url_slug_fallback():
    slug = _url_slug("https://jamanetwork.com/journals/jama/fullarticle/2466145")
    assert slug == "jamanetwork.com_2466145"
    # bare host still yields a usable segment
    assert _url_slug("https://example.org/") == "example.org"


def test_load_prompt_substitutes_url(tmp_path):
    p = tmp_path / "task_prompt.txt"
    p.write_text("Open {url} and download the protocol.", encoding="utf-8")
    assert load_prompt(p, "https://x/y") == "Open https://x/y and download the protocol."
