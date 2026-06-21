import sys
import types

from web_download import downloader
from web_download.config import Settings


def test_efficiency_defaults():
    s = Settings()
    assert s.agent_use_vision is False
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
            return types.SimpleNamespace(usage="usage")

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
    monkeypatch.setattr(downloader, "load_prompt", lambda path, url: "TASK")

    settings = Settings(downloads_dir=tmp_path)
    paths, error = await downloader.download_article(
        "https://x/a", tmp_path / "10.1_x", settings
    )

    assert error is None and paths == []  # nothing actually downloaded by the stub
    assert captured["agent"]["use_vision"] is False
    assert captured["agent"]["flash_mode"] is True
    assert captured["agent"]["max_actions_per_step"] == 8
    assert captured["agent"]["step_timeout"] == 90
    assert captured["browser"]["auto_download_pdfs"] is False
    assert captured["browser"]["wait_between_actions"] == 0.3
    assert captured["max_steps"] == settings.agent_max_steps
