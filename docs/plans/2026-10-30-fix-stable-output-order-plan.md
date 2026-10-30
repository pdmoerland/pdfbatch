---
title: Stable output order
type: fix
status: completed
date: 2026-10-30
iteration: 15
---

# pdfbatch iteration 15: stable output order

Oct 30, 2026 · @Perry Moerland

Found in iteration 14: `papers.json` lists records in the order worker threads finish them, so two runs over the same folder can write the same records in a different order. That makes the file, the source of truth, needlessly hard to diff or keep under version control.

## Fix

1. `write_json` sorts by the same `sort_key` as `papers.ris` and `papers.md` (first author, year, title), so all three list papers in one order.
2. `sort_key` gets the PDF path as a last tie-breaker. Without it, records that tie (identical copies of one PDF have the same author, year and title) keep the order they arrived in, in all three files.

No field changes; only the order of records in `papers.json`, and of tied records in all three files.

**Test:** `test_outputs_are_sorted`: records handed to the writers in two different orders give byte-identical `papers.json` and `papers.ris`, with tied copies ordered by path.

## Result

`d8932cf`. 70 tests and Ruff pass. The reading lists (92 PDFs) give the same records as before, and two runs now write byte-identical `papers.json` and `papers.ris`.

## Done when

- Test first, then the fix, one commit; `uv run pytest` and Ruff pass.
- The reading lists, run twice with `--force`, give byte-identical `papers.json`, and the same records as before.
- `design_decisions_and_rationale.md` ("Outputs") and the history table updated; `status: completed`.
