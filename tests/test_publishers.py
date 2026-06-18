import pytest

from web_download.publishers import get_adapter
from web_download.publishers.base import absolutize, file_type_of, host_matches


def test_registry_routing():
    assert get_adapter("https://jamanetwork.com/journals/jama/fullarticle/2466145").name == "jama"
    assert get_adapter("https://www.nejm.org/doi/full/10.1056/NEJMoa123").name == "nejm"
    assert get_adapter("https://www.thelancet.com/journals/lancet/article/x").name == "lancet"
    assert get_adapter("https://www.bmj.com/content/380/bmj-2022-1").name == "bmj"
    assert get_adapter("https://example.com/article/1") is None


def test_host_matches_subdomains_only():
    assert host_matches("https://www.bmj.com/x", "bmj.com")
    assert host_matches("https://bmj.com/x", "bmj.com")
    assert not host_matches("https://notbmj.com/x", "bmj.com")
    assert not host_matches("https://bmj.com.evil.com/x", "bmj.com")


def test_file_type_of():
    assert file_type_of("https://x/y/file.pdf") == "pdf"
    assert file_type_of("https://x/y/file.PDF?download=1") == "pdf"
    assert file_type_of("https://x/y/file.docx#frag") == "docx"
    assert file_type_of("https://x/y/page") == ""


def test_absolutize():
    assert absolutize("https://x.com/a/b", "/c.pdf") == "https://x.com/c.pdf"
    assert absolutize("https://x.com/a/b", "https://y.com/c.pdf") == "https://y.com/c.pdf"


@pytest.mark.asyncio
async def test_collect_links_with_browser():
    """Browser-backed: only links with a file type or supplement-y text are kept."""
    pytest.importorskip("playwright.async_api")
    from playwright.async_api import async_playwright

    from web_download.publishers.jama import JamaAdapter

    # Mirrors JAMA's real ArticleDataSupplements markup (collapsed/hidden items).
    html = """
    <html><body>
      <div class="widget-ArticleDataSupplements" style="display:none">
        <div class="supplement">
          <a href="/cdn/supp1_prod.pdf" class="supplement-download">
            <div class="supplement-label">pdf</div></a>
          Supplement 1.Trial Protocol
        </div>
        <div class="supplement">
          <a href="/cdn/supp3_prod.pdf" class="supplement-download">
            <div class="supplement-label">pdf</div></a>
          Supplement 3.eTables
        </div>
      </div>
      <a href="/author/jane">Jane Author</a>
    </body></html>
    """
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Exception:
            pytest.skip("chromium not installed")
        page = await browser.new_page()
        await page.goto("about:blank")
        await page.set_content(html)
        found = await JamaAdapter().find_supplements(page)
        await browser.close()

    labels = {c.label for c in found}
    urls = {c.url for c in found}
    assert len(found) == 2
    assert any("Trial Protocol" in l for l in labels)
    assert any("eTables" in l for l in labels)
    assert all("Jane Author" not in l for l in labels)
    assert all(u.endswith("_prod.pdf") for u in urls)  # only supplement files
    assert all(not l.lower().startswith("pdf ") for l in labels)  # icon text stripped
