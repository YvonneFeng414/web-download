```
uv run search-papers --status COMPLETED --out downloads/papers.json --shards-dir downloads/ct-gov -v
```

```
uv run web-download --papers downloads/papers_nejm.json --out downloads/nejm --publisher nejm --start 943 --limit 500 -v
```