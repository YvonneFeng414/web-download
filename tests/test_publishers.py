from web_download.config import Settings
from web_download.publishers import PUBLISHERS, build_task_prompt, detect_publisher

# Text unique to each publisher's fragment, used to assert isolation.
MARKERS = {
    "jama": "Supplemental Content",
    "nejm": "nejm.org",
    "lancet": "thelancet.com",
    "bmj": "Data supplement",
}


def test_detect_from_article_host():
    jama = "https://jamanetwork.com/journals/jama/fullarticle/2466145"
    assert detect_publisher(jama) == "jama"
    assert detect_publisher("https://www.nejm.org/doi/full/10.1056/NEJMoa1") == "nejm"
    assert detect_publisher("https://www.thelancet.com/journals/lancet/x") == "lancet"
    # BMJ's per-journal subdomains resolve too
    assert detect_publisher("https://bmjopen.bmj.com/content/12/1/e001") == "bmj"


def test_detect_returns_none_for_unknown_host():
    assert detect_publisher("https://example.org/article/1") is None


def test_detect_from_doi_org_url():
    """Batch runs pass bare doi.org URLs, so the DOI prefix is the only signal."""
    assert detect_publisher("https://doi.org/10.1001/jama.2016.1") == "jama"
    assert detect_publisher("https://doi.org/10.1056/NEJMoa2035389") == "nejm"
    assert detect_publisher("https://doi.org/10.1136/bmj.n1") == "bmj"
    assert detect_publisher("https://doi.org/10.1016/S0140-6736(23)00222-2") == "lancet"


def test_detect_ignores_non_lancet_elsevier_doi():
    # 10.1016 is Elsevier-wide; only the Lancet's own suffix maps to lancet
    assert detect_publisher("https://doi.org/10.1016/j.jpeds.2020.02.024") is None


def test_detect_accepts_explicit_doi_argument():
    assert detect_publisher("https://doi.org/redirect", doi="10.1136/bmj.n1") == "bmj"


def test_prompt_carries_only_the_matching_publisher_notes():
    settings = Settings()
    for key, marker in MARKERS.items():
        prompt = build_task_prompt("https://doi.org/10.9999/x", settings, publisher=key)
        assert marker in prompt
        for other, other_marker in MARKERS.items():
            if other != key:
                assert other_marker not in prompt


def test_unknown_publisher_gets_generic_notes():
    prompt = build_task_prompt("https://example.org/article/1", Settings())
    assert "Unknown publisher" in prompt
    for marker in MARKERS.values():
        assert marker not in prompt


def test_explicit_publisher_overrides_detection():
    settings = Settings()
    url = "https://jamanetwork.com/journals/jama/fullarticle/2466145"
    prompt = build_task_prompt(url, settings, publisher="bmj")
    assert MARKERS["bmj"] in prompt
    assert MARKERS["jama"] not in prompt


def test_prompt_substitutes_url_and_is_shorter_than_the_all_publisher_body():
    settings = Settings()
    url = "https://doi.org/10.1001/jama.2016.1"
    prompt = build_task_prompt(url, settings)
    assert url in prompt
    assert "{publisher_notes}" not in prompt
    # every fragment concatenated would be strictly larger than the one used
    all_notes = sum(
        len((settings.publisher_notes_dir / f"{k}.txt").read_text(encoding="utf-8"))
        for k in PUBLISHERS
    )
    body = len(settings.prompt_path.read_text(encoding="utf-8"))
    assert len(prompt) < body + all_notes
