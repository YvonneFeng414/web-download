import sys
import types

import pytest

from web_download import downloader
from web_download.config import Settings


def test_efficiency_defaults():
    s = Settings()
    assert s.agent_flash_mode is True
    assert s.auto_download_pdfs is False
    assert s.agent_max_actions_per_step == 8
    assert s.agent_use_vision is False  # DOM-only: the biggest token/latency win
    assert s.agent_use_judge is False  # verify_and_filter is the check we act on
    # a step must outlast the LLM call it waits on, or it aborts first
    assert s.agent_step_timeout > s.agent_llm_timeout


def _install_fake_browser_use(
    monkeypatch, captured, *, is_successful=True, errors=(), on_run=None
):
    """Stub `browser_use` + `browser_use.llm` so download_article runs offline.

    ``on_run`` lets a test simulate what a real agent leaves on disk.
    """

    class FakeBrowser:
        def __init__(self, **kwargs):
            captured["browser"] = kwargs

        async def kill(self):
            pass

    class FakeHistory:
        usage = types.SimpleNamespace(total_cost=0.0123, total_tokens=4567)

        def is_successful(self):
            return is_successful

        def errors(self):
            return list(errors)

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["agent"] = kwargs

        async def run(self, max_steps=None):
            captured["max_steps"] = max_steps
            if on_run is not None:
                on_run()
            return FakeHistory()

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            self.model = kwargs.get("model")
            captured.setdefault("llms", []).append(kwargs)

        async def ainvoke(self, messages, output_format=None, **kwargs):
            raise NotImplementedError

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
    outcome = await downloader.download_article(
        "https://x/a", tmp_path / "10.1_x", settings
    )

    # the stub downloads nothing while claiming success, which is now an error
    assert outcome.paths == [] and "no file reached disk" in (outcome.error or "")
    assert (outcome.cost_usd, outcome.tokens) == (0.0123, 4567)  # usage from history
    # the knobs are forwarded from settings
    agent = captured["agent"]
    assert agent["use_vision"] == settings.agent_use_vision
    assert agent["use_judge"] == settings.agent_use_judge
    assert agent["flash_mode"] == settings.agent_flash_mode
    assert agent["max_failures"] == settings.agent_max_failures
    assert agent["max_actions_per_step"] == settings.agent_max_actions_per_step
    assert agent["llm_timeout"] == settings.agent_llm_timeout
    assert agent["step_timeout"] == settings.agent_step_timeout
    assert captured["browser"]["auto_download_pdfs"] == settings.auto_download_pdfs
    assert captured["browser"]["wait_between_actions"] == settings.wait_between_actions
    assert captured["max_steps"] == settings.agent_max_steps


async def test_download_article_wires_fallback_llm(tmp_path, monkeypatch):
    """WD_FALLBACK_BASE_URL builds a second provider browser-use can switch to."""
    captured: dict = {}
    _install_fake_browser_use(monkeypatch, captured)
    monkeypatch.setattr(downloader, "build_task_prompt", lambda *a, **kw: "TASK")

    settings = Settings(downloads_dir=tmp_path)
    await downloader.download_article("https://x/a", tmp_path / "10.1_x", settings)
    assert captured["agent"]["fallback_llm"] is None  # unset by default

    captured.clear()
    settings = Settings(
        downloads_dir=tmp_path,
        fallback_base_url="https://api.openai.com/v1",
        fallback_model="gpt-5.4-mini",
    )
    await downloader.download_article("https://x/a", tmp_path / "10.1_x", settings)
    assert captured["agent"]["fallback_llm"] is not None
    assert captured["llms"][-1]["base_url"] == "https://api.openai.com/v1"
    assert captured["llms"][-1]["model"] == "gpt-5.4-mini"


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
    must still be returned so the verifier runs on it — a before/after diff misses
    it — but flagged as pre-existing rather than credited to this run."""
    _install_fake_browser_use(monkeypatch, {})
    monkeypatch.setattr(downloader, "build_task_prompt", lambda *a, **kw: "TASK")

    dest = tmp_path / "10.1_x"
    dest.mkdir(parents=True)
    (dest / "mmc1.pdf").write_bytes(b"%PDF-1.4 left by a prior run")

    settings = Settings(downloads_dir=tmp_path)
    outcome = await downloader.download_article("https://x/a", dest, settings)
    # The run touched nothing, so its success claim is not credited to the file
    # an earlier run left behind.
    assert "no file reached disk" in (outcome.error or "")
    assert outcome.paths == [str(dest / "mmc1.pdf")]
    assert outcome.preexisting == [str(dest / "mmc1.pdf")]


async def test_download_article_flags_failed_run_over_stale_file(tmp_path, monkeypatch):
    """The bug this guards: agent.run() returns instead of raising when the agent
    gives up, so a six-week-old PDF used to be reported as a fresh download."""
    _install_fake_browser_use(
        monkeypatch,
        {},
        is_successful=None,  # never reached a `done` action
        errors=["LLM call timed out after 75 seconds."],
    )
    monkeypatch.setattr(downloader, "build_task_prompt", lambda *a, **kw: "TASK")

    dest = tmp_path / "10.1_x"
    dest.mkdir(parents=True)
    (dest / "old.pdf").write_bytes(b"%PDF-1.4 left by a prior run")

    outcome = await downloader.download_article(
        "https://x/a", dest, Settings(downloads_dir=tmp_path)
    )
    assert outcome.error is not None
    assert "timed out" in outcome.error
    assert outcome.preexisting == [str(dest / "old.pdf")]


async def test_download_article_noisy_trace_with_a_download_is_a_success(
    tmp_path, monkeypatch
):
    """A file written during the run outweighs errors earlier in the trace."""
    dest = tmp_path / "10.1_x"

    def _write_during_run(**kwargs):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "protocol.pdf").write_bytes(b"%PDF-1.4 fresh")

    captured: dict = {}
    _install_fake_browser_use(
        monkeypatch, captured, is_successful=None, errors=["some step blew up"]
    )
    monkeypatch.setattr(downloader, "build_task_prompt", lambda *a, **kw: "TASK")
    monkeypatch.setattr(
        downloader, "_mtimes", _spy_mtimes(downloader._mtimes, _write_during_run)
    )

    outcome = await downloader.download_article(
        "https://x/a", dest, Settings(downloads_dir=tmp_path)
    )
    assert outcome.error is None
    assert outcome.paths == [str(dest / "protocol.pdf")]
    assert outcome.preexisting == []


def _spy_mtimes(real, on_first_call):
    """Run `on_first_call` after the pre-run snapshot, simulating a download."""
    calls = {"n": 0}

    def wrapper(directory):
        result = real(directory)
        calls["n"] += 1
        if calls["n"] == 1:
            on_first_call()
        return result

    return wrapper


async def test_download_article_rejects_a_success_claim_with_no_file(
    tmp_path, monkeypatch
):
    """The NEJM failure: the agent clicks a supplement's "Download" link, which
    only opens the PDF inline, then calls done(success=True). Nothing was saved,
    so the run must not be recorded as clean."""
    _install_fake_browser_use(monkeypatch, {}, is_successful=True)
    monkeypatch.setattr(downloader, "build_task_prompt", lambda *a, **kw: "TASK")

    settings = Settings(downloads_dir=tmp_path)
    outcome = await downloader.download_article(
        "https://x/a", tmp_path / "10.1_x", settings
    )
    assert "no file reached disk" in (outcome.error or "")


async def test_download_article_trusts_a_success_claim_backed_by_a_file(
    tmp_path, monkeypatch
):
    """...and the guard stays out of the way when the download is real."""
    dest = tmp_path / "10.1_x"
    _install_fake_browser_use(
        monkeypatch,
        {},
        on_run=lambda: (dest / "protocol.pdf").write_bytes(b"%PDF-1.4"),
    )
    monkeypatch.setattr(downloader, "build_task_prompt", lambda *a, **kw: "TASK")

    outcome = await downloader.download_article(
        "https://x/a", dest, Settings(downloads_dir=tmp_path)
    )
    assert outcome.error is None
    assert outcome.paths == [str(dest / "protocol.pdf")]


async def test_download_article_ignores_hidden_files(tmp_path, monkeypatch):
    dest = tmp_path / "10.1_x"
    dest.mkdir(parents=True)
    (dest / ".DS_Store").write_bytes(b"junk")

    # Written during the run, so this is a genuine download rather than a leftover.
    _install_fake_browser_use(
        monkeypatch, {}, on_run=lambda: (dest / "protocol.pdf").write_bytes(b"%PDF-1.4")
    )
    monkeypatch.setattr(downloader, "build_task_prompt", lambda *a, **kw: "TASK")

    settings = Settings(downloads_dir=tmp_path)
    outcome = await downloader.download_article("https://x/a", dest, settings)
    assert outcome.error is None
    assert outcome.paths == [str(dest / "protocol.pdf")]  # .DS_Store excluded


async def test_shared_browser_survives_and_staging_is_sorted(tmp_path, monkeypatch):
    """A caller-owned browser is left running; its downloads land in the DOI dir."""
    captured: dict = {}
    staging = tmp_path / ".staging"
    staging.mkdir()
    dest = tmp_path / "10.1_x"

    # The agent "downloads" into the shared staging dir, as a real run would.
    def download_into_staging():
        (staging / "protocol.pdf").write_bytes(b"%PDF-1.7 protocol")
        (staging / "half.pdf.crdownload").write_bytes(b"partial")

    _install_fake_browser_use(monkeypatch, captured, on_run=download_into_staging)
    monkeypatch.setattr(downloader, "build_task_prompt", lambda *a, **kw: "TASK")

    class SharedBrowser:
        killed = False

        async def kill(self):
            SharedBrowser.killed = True

    outcome = await downloader.download_article(
        "https://x/a",
        dest,
        Settings(downloads_dir=tmp_path),
        browser=SharedBrowser(),
        staging_dir=staging,
    )

    assert outcome.paths == [str(dest / "protocol.pdf")]  # moved out of staging
    assert not (staging / "protocol.pdf").exists()
    assert (staging / "half.pdf.crdownload").exists()  # partials are left behind
    assert outcome.preexisting == []
    assert SharedBrowser.killed is False  # the caller owns the lifetime


async def test_shared_run_starts_with_an_empty_download_ledger(tmp_path, monkeypatch):
    """A shared session must not hand article N what article N-1 downloaded.

    browser-use mirrors the session's ledger into the agent's
    available_file_paths, which the task prompt treats as proof a download
    happened — and `_drain_staging` has already emptied the staged path it names.
    """
    captured: dict = {}
    staging = tmp_path / ".staging"
    staging.mkdir()
    seen_by_the_agent: list[list[str]] = []

    class SharedBrowser:
        # What an earlier article in the batch left behind.
        _downloaded_files = ["/old/.staging/mmc1.pdf"]

        async def kill(self):  # pragma: no cover - the caller owns the lifetime
            pass

    browser = SharedBrowser()

    def download_into_staging():
        # Snapshot DURING the run: clearing it afterwards would be too late.
        seen_by_the_agent.append(list(browser._downloaded_files))
        (staging / "mmc1.pdf").write_bytes(b"%PDF-1.7 protocol")

    _install_fake_browser_use(monkeypatch, captured, on_run=download_into_staging)
    monkeypatch.setattr(downloader, "build_task_prompt", lambda *a, **kw: "TASK")

    outcome = await downloader.download_article(
        "https://x/a",
        tmp_path / "10.1_x",
        Settings(downloads_dir=tmp_path),
        browser=browser,
        staging_dir=staging,
    )

    assert seen_by_the_agent == [[]]
    assert outcome.paths == [str(tmp_path / "10.1_x" / "mmc1.pdf")]


def test_forget_tracked_downloads_tolerates_a_missing_ledger():
    """A browser-use rename must degrade to the old behaviour, not kill a batch."""
    downloader._forget_tracked_downloads(object())


async def test_shared_browser_requires_staging_dir(tmp_path, monkeypatch):
    captured: dict = {}
    _install_fake_browser_use(monkeypatch, captured)

    with pytest.raises(ValueError, match="staging_dir"):
        await downloader.download_article(
            "https://x/a", tmp_path / "d", Settings(), browser=object()
        )


def test_build_shared_browser_keeps_the_session_alive(tmp_path, monkeypatch):
    """keep_alive is what stops browser-use closing it after each agent.run()."""
    captured: dict = {}
    _install_fake_browser_use(monkeypatch, captured)
    staging = tmp_path / ".staging"

    downloader.build_shared_browser(Settings(downloads_dir=tmp_path), staging)

    assert captured["browser"]["keep_alive"] is True
    assert captured["browser"]["downloads_path"] == str(staging.resolve())
    assert staging.is_dir()  # created up front, before the browser needs it
