from web_download import pipeline
from web_download.config import Settings
from web_download.pipeline import _doi_slug, _url_slug
from web_download.publishers import load_prompt


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


def test_load_prompt_substitutes_publisher_notes(tmp_path):
    p = tmp_path / "task_prompt.txt"
    p.write_text("Open {url}. Notes:\n{publisher_notes}\nDone.", encoding="utf-8")
    assert load_prompt(p, "https://x/y", "   - JAMA: top of page") == (
        "Open https://x/y. Notes:\n   - JAMA: top of page\nDone."
    )


def _capture_download(monkeypatch, captured):
    async def fake_download(url, dest_dir, settings, *, publisher=None):
        captured["publisher"] = publisher
        return [], None, 0.0, 0

    monkeypatch.setattr(pipeline, "download_article", fake_download)


async def test_run_detects_publisher_from_doi(tmp_path, monkeypatch):
    captured: dict = {}
    _capture_download(monkeypatch, captured)
    await pipeline.run(
        "https://doi.org/10.1056/NEJMoa2035389", Settings(downloads_dir=tmp_path)
    )
    assert captured["publisher"] == "nejm"


async def test_run_honors_configured_publisher(tmp_path, monkeypatch):
    """--publisher / WD_PUBLISHER wins over what the URL would suggest."""
    captured: dict = {}
    _capture_download(monkeypatch, captured)
    settings = Settings(downloads_dir=tmp_path, publisher="bmj")
    await pipeline.run("https://jamanetwork.com/journals/jama/fullarticle/2466145", settings)
    assert captured["publisher"] == "bmj"
