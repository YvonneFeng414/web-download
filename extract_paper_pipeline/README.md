# Trial → paper extraction pipeline

Builds a dataset linking completed ClinicalTrials.gov trials to the papers that
report them, adds PubMed journal and date information, and selects the
"R1" set: papers in NEJM, Lancet, JAMA and BMJ published after the trial's
primary completion date.

```
ClinicalTrials.gov API ──► 01 ──► studies.jsonl
                                   │
                                   ▼
                          02 ──► papers_raw.json      (one record per trial–paper pair)
                                   │
PubMed E-utilities ─────► 03 ──► papers.json          (+ journal_name, date)
                                   │
                                   ▼
                          04 ──► r1_papers.csv        (+ diff_days, doi)
                                 journal_counts.csv
```

## Setup

```bash
pip install -r requirements.txt     # requests, pandas, tqdm
```

No API keys are required. Step 3 is faster with an NCBI key: `export NCBI_API_KEY=...`
(10 requests/s instead of 3).

## Run

Run from this folder. Every script writes to `./data/` by default, and every
input and output path can be overridden with `--studies/--papers/--out`. Use `-h` for all options.

```bash
python 01_fetch_studies.py            # ~300k completed studies, ~5-10 min
python 02_extract_papers.py
python 03_fetch_pubmed_metadata.py    # ~150k PMIDs, ~5-15 min without a key
python 04_select_r1_papers.py
```

Quick smoke test on a small sample:

```bash
python 01_fetch_studies.py --max-studies 500 --out data/s.jsonl
python 02_extract_papers.py --studies data/s.jsonl --out data/s_raw.json
python 03_fetch_pubmed_metadata.py --papers data/s_raw.json --out data/s_papers.json
python 04_select_r1_papers.py --papers data/s_papers.json --out data/s_r1.csv
```

## Steps

### 01 — fetch studies (`01_fetch_studies.py`)
Pages through `https://clinicaltrials.gov/api/v2/studies` with
`filter.overallStatus=COMPLETED` (`--status`). It requests only `NCTId`,
`statusModule` and `referencesModule`. Output is JSON Lines with one study per
line, written as pages arrive. Network errors, HTTP 429 and HTTP 5xx are
retried with backoff.

### 02 — extract papers (`02_extract_papers.py`)
For each study, keeps every reference that has a PMID and a citation, and whose
type is `RESULT` (results publication added by the sponsor) or `DERIVED`
(matched by NLM using the NCT number). `BACKGROUND` references are dropped.

### 03 — PubMed metadata → `papers.json` (`03_fetch_pubmed_metadata.py`)
Looks up each unique PMID with `esummary`, 200 per request, and adds
`journal_name` and `date` to every record. The output is `papers.json`:

| key | example | source |
|---|---|---|
| `nctId` | `NCT03547791` | ClinicalTrials.gov |
| `paper_title` | `Lee SM, ... JAMA Pediatr. 2025 Dec 1;179(12):1275-1282. doi: 10.1001/...` | the full citation string |
| `pmid` | `"40982289"` (string) | ClinicalTrials.gov |
| `ref_type` | `RESULT` / `DERIVED` | ClinicalTrials.gov |
| `primary_completion_date` | `2023-12-31` or `2023-12` | ClinicalTrials.gov |
| `study_completion_date` | `2024-07-26` | ClinicalTrials.gov |
| `journal_name` | `JAMA pediatrics` | PubMed `fulljournalname` |
| `date` | `2025/09/22 12:46` | PubMed history, `pubstatus == "pubmed"` |

`journal_name` and `date` are `null` when PubMed has no value.

### 04 — select R1 papers (`04_select_r1_papers.py`)
Keeps rows where
- `diff_days > 0`. `diff_days` is the PubMed date minus the primary completion
  date, in days. Both dates are first truncated to the first of the month,
  because many completion dates have only a month.
- `journal_name` is one of `The New England journal of medicine`,
  `Lancet (London, England)`, `JAMA` or `BMJ (Clinical research ed.)`
  (`--journals`).
- `ref_type` is `RESULT` or `DERIVED` (`--ref-types`).
- Optional: `--min-pub-date 2011-01-01`.

It adds `doi`, parsed from the `doi: 10.xxxx/...` part of the citation. It also
writes `journal_counts.csv`, the number of papers per journal before
filtering, which helps when choosing journals.

## Verification against the original outputs

- **Step 2** run on the original `studies.json` (309,303 studies) gave the same
  210,069 records, in the same order and with the same values, as the original
  `papers.json`. Step 3 writes the file with the same settings
  (`json.dump(..., indent=2)`), which reproduces the original `papers.json`
  byte for byte.
- **Step 3** on a live sample of 2,500 studies: for every trial–paper pair also
  present in the original `papers.json` (1,459 pairs), `journal_name` and `date`
  were identical.
- **Step 4** run on the original `papers.json` reproduced `R1_data_test.csv`
  exactly: 4,225 rows and 3,878 trials, every cell the same.

A fresh run will not match these files exactly, because ClinicalTrials.gov keeps
adding and editing references. In the live sample, about 10% of trial–paper
pairs were new and about 1% had edited citations.

## Differences from the original scripts

| original | here |
|---|---|
| `api_call.py` capped at 562,284 studies; crashed on an error if no response existed; no retries | No cap. Retries with backoff. Streams to JSONL instead of holding about 1.3 GB in memory. |
| Also fetched `contactsLocationsModule`, used only by the commented-out PI matching | Dropped, which makes the studies file much smaller. This does not change `papers.json`. |
| `data_preprocessing.py` imported an unused, wrong-path `utils.names` | Removed. Accepts `.jsonl` or the old `studies.json`. |
| `pubmed_api_call.py` overwrote `papers.json` in place, sent duplicate PMIDs, and silently left keys out when a batch failed | Writes a separate output. Queries each PMID once using POST. Failed lookups get `null` values. Respects NCBI rate limits. |
| R1 selection was spread across notebook cells | Combined into `04_select_r1_papers.py`, with the filters as options. |
