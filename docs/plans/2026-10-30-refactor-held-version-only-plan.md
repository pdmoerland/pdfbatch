---
title: Only the version you hold
type: refactor
status: completed
date: 2026-10-30
iteration: 10
---

# Iteration 10 — Only the version you hold

## Overview

All extracted data must come from the PDFs in the folder. `--prefer published` broke that: for a preprint with a journal version it replaced the title, journal, volume, pages, OA data and DOI with the journal record's. The option is removed. The other version is still pointed to in a note, as it was by default.

## Changes

- **`cli.py`:** the `--prefer` option is gone.
- **`pipeline.py`:** `Settings.prefer` is gone, and `apply_version_policy` becomes `note_other_version(rec)`: it only adds "Published version exists: https://doi.org/…" (or "Preprint version: …") and no longer fetches the other version.
- **`bench/run_benchmark.py`:** no `--prefer`; a result is correct only when its DOI is the gold DOI, so `related_doi` no longer counts. For a preprint the gold DOI is the preprint's.
- **Docs:** README Preprints section, the design doc's "Preprints and published versions", `bench/README.md`.

Output with the default settings is unchanged.

## Verification

- `test_arxiv_prefer_published` is replaced by `test_published_version_is_only_noted`: the arXiv preprint keeps its own DOI, journal and OA status, gets the note, and the journal DOI is never looked up.
- `test_benchmark_scoring`: the held preprint DOI is correct; a record carrying the journal DOI instead is wrong.
- 57 tests pass offline; `uv run ruff check pdfbatch tests bench` passes.
