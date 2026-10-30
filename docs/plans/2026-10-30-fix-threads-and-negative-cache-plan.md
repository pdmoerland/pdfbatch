---
title: PyMuPDF in one thread at a time, short-lived 404s, checked field names
type: fix
status: completed
date: 2026-10-30
iteration: 17
---

# pdfbatch iteration 17: PyMuPDF lock, short-lived 404s, checked field names

Oct 30, 2026 · @Perry Moerland

Three of four points from an external review. The fourth (send GROBID through `Http`; its BibTeX regex cuts titles at braces) is left: `Http` does cached GETs to public APIs, GROBID is a POST of the PDF to your own server, failures already fall through to the next tier, and the regex keeps "Splicing of {RNA}" whole (tested).

## Fix 1: PyMuPDF in one thread at a time

**Problem:** PyMuPDF does not support multithreaded use, not even with separate documents, and may give wrong results or crash. Four worker threads call `read_pdf` at once; only OCR was locked (Leptonica had already failed that way).

**Fix:** one module lock around the whole of `read_pdf`, the only place PyMuPDF runs in a worker. `_OCR_LOCK` then guards nothing more and goes. The network work stays parallel.

**Cost, measured:** a full cached run of the 92 PDFs of two reading lists took 22–48 s without the lock and 27–33 s with it; the noise is larger than the difference. First runs are dominated by the network.

**Test:** `test_pdfs_are_read_one_at_a_time`: four threads reading a PDF never have two `pymupdf.open` documents in use at once.

## Fix 2: a 404 is cached for a day, not thirty

**Problem:** a DOI that CrossRef or OpenAlex does not know yet (an accepted manuscript printing its DOI before registration) is cached as a 404 for 30 days, so `--retry-unresolved` keeps getting the old miss for a month.

**Fix:** `NOT_FOUND_TTL = 86400` for cached 404s; other answers keep `CACHE_TTL`. Empty search results stay at 30 days: a newly indexed paper turns up in later searches anyway, and `Http` cannot tell an empty result from a full one without knowing each API's format. No `--refresh` flag: with one-day 404s, deleting the cache folder remains the rare manual reset.

**Test:** `test_404_expires_after_a_day`.

## Fix 3: API field names are checked

**Problem:** `_merge` skips any key that is not a `Record` field, so a misspelt field name in an API parser would vanish silently. Today every key is right (checked).

**Fix:** a test, not a `TypedDict` (the project runs no type checker, so nothing would enforce it): every key that the CrossRef, OpenAlex, arXiv and Europe PMC parsers return, on the fixtures' API responses, is a `Record` field or a known private key (`_license`).

## Result

`4988a8a`, `3d47e14`, `d108b71`. 74 tests and Ruff pass. The field-name test was checked to fail on a misspelt `pmc_id`. The reading lists (92 PDFs) give byte-identical `papers.json` to iteration 16's run.

## Done when

- One commit per fix, test first; `uv run pytest` and Ruff pass.
- The reading lists give the same records as before (cached run, compared as sets).
- `design_decisions_and_rationale.md` and the history table updated; `status: completed`.
