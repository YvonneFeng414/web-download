import sys
import types

from web_download import downloader
from web_download.config import Settings


def test_efficiency_defaults():
    s = Settings()
    assert s.agent_flash_mode is True
    assert s.auto_download_pdfs is False
    assert s.agent_max_actions_per_step == 8
    assert s.agent_step_timeout == 90


def _install_fake_browser_use(monkeypatch, captured):
    """Stub `browser_use` + `browser_use.llm` so download_article runs offline."""

    class FakeBrowser:
        def __init__(self, **kwargs):
            captured["browser"] = kwargs

        async def kill(self):
            pass

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["agent"] = kwargs

        async def run(self, max_steps=None):
            captured["max_steps"] = max_steps
            usage = types.SimpleNamespace(total_cost=0.0123, total_tokens=4567)
            return types.SimpleNamespace(usage=usage)

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured["llm"] = kwargs

    bu = types.ModuleType("browser_use")
    bu.Agent = FakeAgent
    bu.Browser = FakeBrowser
    bu_llm = types.ModuleType("browser_use.llm")
    bu_llm.ChatOpenAI = FakeChatOpenAI
    monkeypatch.setitem(sys.modules, "browser_use", bu)
    monkeypatch.setitem(sys.modules, "browser_use.llm", bu_llm)


async def test_download_article_forwards_efficiency_knobs(tmp_path, monkeypatch):
    captured: dict = {}
    _install_fake_browser_use(monkeypatch, captured)
    monkeypatch.setattr(downloader, "build_task_prompt", lambda *a, **kw: "TASK")

    settings = Settings(downloads_dir=tmp_path)
    paths, error, cost, tokens = await downloader.download_article(
        "https://x/a", tmp_path / "10.1_x", settings
    )

    assert error is None and paths == []  # nothing actually downloaded by the stub
    assert (cost, tokens) == (0.0123, 4567)  # usage captured from history
    # the knobs are forwarded from settings
    assert captured["agent"]["use_vision"] == settings.agent_use_vision
    assert captured["agent"]["flash_mode"] == settings.agent_flash_mode
    assert captured["agent"]["max_actions_per_step"] == settings.agent_max_actions_per_step
    assert captured["agent"]["step_timeout"] == settings.agent_step_timeout
    assert captured["browser"]["auto_download_pdfs"] == settings.auto_download_pdfs
    assert captured["browser"]["wait_between_actions"] == settings.wait_between_actions
    assert captured["max_steps"] == settings.agent_max_steps


async def test_download_article_task_carries_one_publisher_only(tmp_path, monkeypatch):
    """The agent's task must hold the selected publisher's notes and no others."""
    captured: dict = {}
    _install_fake_browser_use(monkeypatch, captured)

    settings = Settings(downloads_dir=tmp_path)
    await downloader.download_article(
        "https://doi.org/10.1001/jama.2016.1",
        tmp_path / "10.1001_jama.2016.1",
        settings,
        publisher="jama",
    )

    task = captured["agent"]["task"]
    assert "https://doi.org/10.1001/jama.2016.1" in task
    assert "Supplemental Content" in task  # JAMA note
    assert "Data supplement" not in task  # BMJ note stays out of context
    assert "thelancet.com" not in task


async def test_download_article_reports_preexisting_file(tmp_path, monkeypatch):
    """A file already in the per-DOI folder (e.g. from a re-run that overwrites it)
    must still be returned so the verifier runs on it — a before/after diff misses it."""
    _install_fake_browser_use(monkeypatch, {})
    monkeypatch.setattr(downloader, "build_task_prompt", lambda *a, **kw: "TASK")

    dest = tmp_path / "10.1_x"
    dest.mkdir(parents=True)
    (dest / "mmc1.pdf").write_bytes(b"%PDF-1.4 left by a prior run")

    settings = Settings(downloads_dir=tmp_path)
    paths, error, _cost, _tokens = await downloader.download_article(
        "https://x/a", dest, settings
    )
    assert error is None
    assert paths == [str(dest / "mmc1.pdf")]


async def test_download_article_ignores_hidden_files(tmp_path, monkeypatch):
    _install_fake_browser_use(monkeypatch, {})
    monkeypatch.setattr(downloader, "build_task_prompt", lambda *a, **kw: "TASK")

    dest = tmp_path / "10.1_x"
    dest.mkdir(parents=True)
    (dest / ".DS_Store").write_bytes(b"junk")
    (dest / "protocol.pdf").write_bytes(b"%PDF-1.4")

    settings = Settings(downloads_dir=tmp_path)
    paths, error, _cost, _tokens = await downloader.download_article(
        "https://x/a", dest, settings
    )
    assert error is None
    assert paths == [str(dest / "protocol.pdf")]  # .DS_Store excluded
