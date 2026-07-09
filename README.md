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
  citation (regex) and turned into `https://doi.org/<doi>`, which 302-redirects to
  the publisher's article page. No API key or quota required; titles without a DOI
  are skipped.

```
title / citation ──(regex DOI → doi.org)──▶ article URL ──▶ browser-use agent
                                                              │  reads the page,
                                                              ▼  downloads protocol/SAP
                            downloads/<doi>/…pdf   (+ downloads/bookkeeping.csv at the --out root)
```

## Setup

```bash
uv sync --extra dev
uv run playwright install chromium   # browser the agent drives
cp .env.example .env                  # add OPENAI_API_KEY (+ optional BASE_URL)
```

## Usage

```bash
uv run python main.py https://jamanetwork.com/journals/jama/fullarticle/2466145 -v
# or, after install:
uv run web-download <url> --out downloads -v
```

Flags: `--out DIR` (output directory), `--headed` (show the browser), `-v`
(verbose); plus `--start N` / `--limit N` for `--papers` batches.

### Starting from a title instead of a URL

When you only have a citation/title, the resolver extracts its DOI and builds
`https://doi.org/<doi>` (which redirects to the article page), then runs the
normal download flow. Titles with no DOI are skipped.

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

## Tuning agent behavior

There are no per-publisher adapters — a single browser-use agent handles every
site, driven entirely by the task prompt at
[prompt-template/task_prompt.txt](prompt-template/task_prompt.txt). To adjust how
the agent finds or filters supplements (e.g. journal-specific hints), edit that
prompt; runtime knobs (model, step budget, verification) live in
[src/web_download/config.py](src/web_download/config.py) and are overridable via
`WD_*` environment variables.
