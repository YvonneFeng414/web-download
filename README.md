# web-download

Pipeline that, given a medical-journal article URL, finds the downloadable
supplementary PDFs, skims only their first few pages to decide whether each is a
**study protocol** (or statistical analysis plan), and downloads the ones that
are.

## Architecture

- **patchright (stealth Playwright)** — deterministic browser engine: navigation,
  locating supplement links, cookie-aware file fetches. Plain Playwright cannot
  clear publisher anti-bot challenges (e.g. JAMA's Cloudflare "Just a
  moment..."); patchright in **headed** mode does. Hence `WD_HEADLESS` defaults
  to `false`.
- **browser-use** — an LLM-agent layer built *on top of* Playwright. Used only as
  a **fallback** to locate supplement links when no publisher adapter matches.
- **PyMuPDF + OpenAI GPT** — the "is it a protocol?" judgment is about PDF
  *content*, which the browsers don't read. We download the file, extract the
  first few pages, and classify them with GPT.

```
article URL
   │  Playwright: open page
   ▼
publisher adapter (JAMA / NEJM / Lancet / BMJ)  ──none?──▶ browser-use agent fallback
   │  supplement candidates (label + url)
   ▼
PyMuPDF: download PDF, extract first N pages
   ▼
OpenAI GPT: is_protocol? doc_type? confidence?
   ▼
save PDFs judged protocol above the confidence threshold
```

## Setup

```bash
uv sync --extra dev
uv run patchright install chromium   # stealth driver's browser
uv run playwright install chromium   # used by the browser-use fallback / tests
cp .env.example .env                  # add your OPENAI_API_KEY
```

## Usage

```bash
uv run python main.py https://jamanetwork.com/journals/jama/fullarticle/2466145 -v
# or, after install:
uv run web-download <url> --out downloads --skim-pages 3 --threshold 0.6
```

## Tests

```bash
uv run pytest          # pure-logic tests run offline; browser test skips if chromium absent
```

## Adding a publisher

Subclass `PublisherAdapter` in `src/web_download/publishers/`, implement
`domains` and `find_supplements`, and register it in
`src/web_download/publishers/__init__.py`. The pipeline core is unchanged.
