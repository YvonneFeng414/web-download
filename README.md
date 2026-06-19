# web-download

Pipeline that, given a medical-journal article (URL, title/citation, or a batch
`papers.json`), runs a **browser-use agent** to find the article's supplementary
materials and download the **study protocol** / **statistical analysis plan**.

## Architecture

- **browser-use agent** — a single LLM-driven browser agent. Given the article
  URL and a general task prompt ([prompt-template/task_prompt.txt](prompt-template/task_prompt.txt)),
  it navigates the page, finds the supplementary-material section, and downloads
  the protocol/SAP itself. Runs **headed** by default (`WD_HEADLESS=false`) so
  publisher anti-bot challenges (e.g. JAMA's Cloudflare) can clear; a persistent
  profile (`WD_USER_DATA_DIR`) caches that clearance across runs.
- **resolution** — for `--title`/`--papers`, the DOI is extracted from the
  citation and resolved to the article URL via Google Custom Search
  (`GOOGLE_API_KEY` + `CX_ID`).

```
title / citation ──(DOI → Google)──▶ article URL ──▶ browser-use agent
                                                         │  reads the page,
                                                         ▼  downloads protocol/SAP
                                         downloads/<doi>/…pdf  +  bookkeeping.csv
```

## Setup

```bash
uv sync --extra dev
uv run playwright install chromium   # browser the agent drives
cp .env.example .env                  # add OPENAI_API_KEY (+ BASE_URL), GOOGLE_API_KEY, CX_ID
```

## Usage

```bash
uv run python main.py https://jamanetwork.com/journals/jama/fullarticle/2466145 -v
# or, after install:
uv run web-download <url> --out downloads --skim-pages 3 --threshold 0.6
```

### Starting from a title instead of a URL

When you only have a citation/title, the resolver extracts its DOI and uses
Google Custom Search (set `GOOGLE_API_KEY` + `CX_ID` in `.env`) to find the
article URL, then runs the normal download flow. Titles with no DOI are skipped.

```bash
# single title
uv run web-download --title "…citation… doi: 10.1016/j.jpeds.2020.02.024" -v

# batch over the papers.json produced by `search-papers`
uv run web-download --papers downloads/papers.json --limit 3 -v
```

Downloads are grouped per article into a DOI-named subfolder under `--out`
(e.g. `downloads/10.1016_j.jpeds.2020.02.024/…pdf`); when no DOI is available the
article id is used instead.

## Tests

```bash
uv run pytest          # pure-logic tests run offline; browser test skips if chromium absent
```

## Adding a publisher

Subclass `PublisherAdapter` in `src/web_download/publishers/`, implement
`domains` and `find_supplements`, and register it in
`src/web_download/publishers/__init__.py`. The pipeline core is unchanged.
