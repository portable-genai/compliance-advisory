# Manual source drop-box

Some publishers gate direct PDF downloads behind a JS-driven repository (HKMA's
Banking Regulatory Document Repository does this for every `/media/` PDF path), so the
corpus fetcher cannot download them headlessly and refuses to index the repository's
HTML shell in their place.

To ingest such a source: open its registry `url` in a browser, download the PDF, and
save it here named `<source id>.pdf` (for example
`hkma-sa-2-cloud-computing-2022.pdf`). The next refresh pass ingests the file instead
of fetching the URL; everything else (redaction, page-level citations, the freshness
ledger) is unchanged.

Downloaded regulator documents are not committed: everything except this README is
git-ignored.
