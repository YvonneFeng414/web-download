from web_download import pipeline
from web_download.config import Settings
from web_download.downloader import DownloadOutcome
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


class FakeBrowser:
    """Stands in for the shared browser-use Browser, recording its lifecycle."""

    def __init__(self):
        self.started = False
        self.visited = None
        self.killed = False

    async def start(self):
        self.started = True

    async def navigate_to(self, url, new_tab=False):
        self.visited = url

    async def kill(self):
        self.killed = True


def _capture_download(monkeypatch, captured, outcome=None):
    async def fake_download(
        url, dest_dir, settings, *, publisher=None, browser=None, staging_dir=None
    ):
        captured["publisher"] = publisher
        captured.setdefault("browsers", []).append(browser)
        captured["staging_dir"] = staging_dir
        return outcome or DownloadOutcome([], [], None, 0.0, 0)

    monkeypatch.setattr(pipeline, "download_article", fake_download)
    browser = FakeBrowser()
    captured["shared"] = browser
    monkeypatch.setattr(pipeline, "build_shared_browser", lambda s, d: browser)
    return browser


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


async def test_run_carries_preexisting_paths_and_error(tmp_path, monkeypatch):
    """A failed agent run over a file left by an earlier run keeps both signals."""
    stale = str(tmp_path / "10.1_x" / "old.pdf")
    _capture_download(
        monkeypatch,
        {},
        DownloadOutcome(
            paths=[stale],
            preexisting=[stale],
            error="agent stopped without finishing",
            cost_usd=0.0,
            tokens=0,
        ),
    )
    settings = Settings(downloads_dir=tmp_path, verify_downloads=False)
    result = await pipeline.run("https://doi.org/10.1/x", settings)

    assert result.error == "agent stopped without finishing"
    assert result.preexisting_paths == [stale]
    assert result.downloaded_paths == [stale]


async def test_batch_reuses_one_browser_across_papers(tmp_path, monkeypatch):
    """One browser for the whole list — the only way a sign-in outlives paper #1."""
    captured: dict = {}
    shared = _capture_download(monkeypatch, captured)
    monkeypatch.setattr(pipeline, "extract_doi", lambda t: "10.1/x")
    monkeypatch.setattr(pipeline, "resolve_article_url", lambda t: "https://x/a")

    settings = Settings(downloads_dir=tmp_path, verify_downloads=False)
    await pipeline.run_titles(["paper one", "paper two"], settings)

    assert captured["browsers"] == [shared, shared]  # same object, not a new one
    assert captured["staging_dir"] == tmp_path / pipeline._STAGING_DIRNAME
    assert shared.killed  # ...and it is closed once the batch is done


async def test_login_pause_navigates_then_waits(tmp_path, monkeypatch):
    """--login opens the browser, optionally navigates, and blocks on the user."""
    captured: dict = {}
    shared = _capture_download(monkeypatch, captured)
    prompts: list = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt))

    settings = Settings(downloads_dir=tmp_path, verify_downloads=False)
    await pipeline.run(
        "https://doi.org/10.1/x",
        settings,
        pause_for_login=True,
        login_url="https://shib.example.edu/idp",
    )

    assert shared.started  # browser is up before the user is asked to sign in
    assert shared.visited == "https://shib.example.edu/idp"
    assert prompts  # and the run waited for them
    assert captured["browsers"] == [shared]


async def test_share_browser_off_gives_each_article_its_own(tmp_path, monkeypatch):
    """The escape hatch: no shared browser, so download_article makes its own."""
    captured: dict = {}
    shared = _capture_download(monkeypatch, captured)

    settings = Settings(
        downloads_dir=tmp_path, verify_downloads=False, share_browser=False
    )
    await pipeline.run("https://doi.org/10.1/x", settings)

    assert captured["browsers"] == [None] and captured["staging_dir"] is None
    assert not shared.started  # never built one
