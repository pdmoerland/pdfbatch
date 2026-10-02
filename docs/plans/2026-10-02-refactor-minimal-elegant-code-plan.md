---
title: Minimal, elegant code — "Beauty is our business"
type: refactor
status: completed
date: 2026-10-02
iteration: 4
---

# Iteration 4 — Minimal, elegant code

## Overview

The aim was a review of the whole package, guided by Dijkstra's *Beauty is our business*: remove redundancy, keep one definition per idea, and keep behaviour intact. The result (`df2dcc4`) goes from 2,395 to 1,772 lines and from 13 modules to 10, while producing byte-identical Markdown and RIS for the test fixtures.

## Method

1. Read every module before changing anything. Then list leftovers from the bot, duplicated logic, detours and no-ops.
2. **Golden outputs first.** Render `papers.md`, `papers.ris` and `papers.json` for the five fixture PDFs under three settings: default, `--unpaywall` and `--prefer published`.
3. Refactor, then compare the outputs again. Markdown and RIS were identical; JSON differed only in the temporary directory name.
4. Run the CLI end to end with mocked APIs, twice, to check incremental reruns and the recognition of a renamed PDF. Exercise the link checker with a fake HEAD request.

## What was found and changed

### Leftovers from the Slack bot

- `link_patterns.py`:
  - **Removed:** an unused `platform_enabled()`, an unused `domains` field, and a `try/except` around normalisers that cannot raise.
  - **Docstrings:** comments about `config.yaml` and Slack rendering removed.
- `link_labeler.py`: a class with configurable keywords that nothing configured, and a docstring about an LLM labeller that doesn't exist here.
- **`Settings`:**
  - `mailto` was never read by the pipeline; `Http` carries it.
  - `blocklist` and `max_links_per_kind` were never set by anyone. They became module constants (`BLOCKLIST`, `MAX_PER_KIND`).

### Duplicated logic

- **URL canonicalisers:** three copy-pasted normalisers for GitHub, Bitbucket and Codeberg, and five more with the same "search, then fill a template" shape. One `_canon(pattern, template)` now replaces them, plus `_repo_host()` for owner/repo platforms. Only GitLab, with its nested groups and `/-/` sub-paths, keeps its own function.
- **GROBID and LLM tiers:** both did "try the DOI; if it doesn't validate, search the title". This is now one `_lookup`.
- **OA rule for preprints:** written twice (preprint-DOI prefix, arXiv ID), now once.
- **Version policy:** it fetched OpenAlex and Unpaywall OA data for the published version, which `enrich()` then fetched again. It now resets OA and lets `enrich()` do it.
- **"Needs review":** `confidence in ("low", "unresolved")` appeared in both the RIS and Markdown writers. It is now `Record.needs_review`.
- **Link positions:** finding a link's position in the text was done twice with different fallbacks; this is now one `_position`.

### Detours and no-ops

- **APA:** the formatter turned `Author` objects back into `"Family, Given"` strings and re-parsed them. It now works on `Author` directly. (`citation.py` was folded into `writers.py`.)
- **Abstract:** `rec.abstract = rec.abstract or keep_abstract` after an override merge could never change anything, because the merge skips empty values.
- **Year:** deriving the year from the date was dead code, because every API that sets a date also sets the year.
- **Guards:** `try: import` guards around `diskcache` and `python-dotenv`, which are required dependencies.
- **Validation:** the PDF text was re-normalised on every comparison. It is now a `cached_property` (`PdfContent.head`) computed once per PDF.
- **Unused fields:** `PdfContent.n_pages` (the same as `len(page_texts)`) and the page index stored with every link annotation.
- **Small things:** the `pdf_batch.py` wrapper duplicated the `pdf-batch` entry point. A `--attach` value was passed through an `if` that returned it unchanged. The LLM backend dispatch is now a dict instead of an if-chain.

## New shape

- **`links.py`:** replaces `link_patterns.py`, `link_labeler.py` and `link_validator.py`, and takes over `find_links` from the pipeline. It holds platforms, extraction, labels, the cached `LinkChecker` and the "is this the paper's own link?" filter.
- **`pipeline.identify`:**

  ```python
  best = _best_candidate(http, s, pdf)                     # tiers 1–2
  tiers = chain([best] if best else [], _fallbacks(...))   # tiers 3–5, lazy
  hit = next((h for h in tiers if h[0] and _good(h[1])), best)
  ```

  Later tiers, and with them GROBID and LLM calls, run only when needed. If nothing validates, the low-confidence `best` is kept with a note.
- **`cli.py`:** argument parsing in `_arguments()`. Crash handling lives inside the worker function. Lazy imports are gone.

## Intentional behaviour changes

Both are bug fixes in edge cases; neither occurs in the fixtures.

- **APA organisation authors:** these are now written as-is. Previously "Human Cell Atlas Consortium" became "Consortium, H. C. A.", and a multi-word family name without a given name was split into initials.
- **RIS:** a record with no DOI or URL but an OA URL no longer gets the same `UR` line twice.

Also note:

- **Cache keys:** the link checker's keys changed, so cached links are probed once more.
- **Canonicalisation:** it is now case-insensitive. `GitHub.com/Org/Repo` is canonicalised instead of being left as is.

## Verification

- 22 tests pass. The new ones are an APA test covering an organisation author, and six URL classification cases.
- Golden Markdown and RIS are byte-identical in all three settings.
- CLI run, then rerun: 5 processed, then 5 cached, including one renamed file.
