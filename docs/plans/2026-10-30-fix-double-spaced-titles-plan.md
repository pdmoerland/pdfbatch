---
title: Double-spaced titles
type: fix
status: completed
date: 2026-10-30
iteration: 13
---

# pdfbatch iteration 13: double-spaced titles

Oct 30, 2026 · @Perry Moerland

Found while fixing iteration 12: for `baggerly_prodcon.pdf` the title guess was only the first of two title lines, "Are the NCI/FDA Ovarian Proteomic Data". Since that PDF is now (rightly) unresolved, the truncated guess became its title in `papers.md` and `papers.ris`.

## Cause

`_title_guesses` in `pdf_reader.py` joins lines of the same font size until the distance between two line tops exceeds 2.2 font sizes. The report is a double-spaced LaTeX technical report: its 17.2 pt title lines are 43.8 pt apart (2.54 sizes), so the run stopped after line 1.

## Why not just raise the limit

Measured on the 94 PDFs of the `_PDM/Reader` folders and `Downloads/papers`: at 2.6 or 3.0 the Baggerly title is whole, but `Knij04a.pdf` loses its title guess altogether. It is a Word manuscript typed in 11.3 pt throughout, with blank lines between paragraphs, and the same 2.3-size gap that separates its title from the authors would let the run carry on through authors and affiliations until it is too long to be a title.

## Fix

Allow up to 3.0 sizes between title lines only for a display font, one at least 1.2 × the body font (the size with the most characters in the top 60% of page 1). A body-size "title" keeps the 2.2 limit. On the 94 PDFs this changes exactly one title guess: Baggerly's, which is now the full title.

**Test:** `test_double_spaced_title`, one page per case: a double-spaced title in a display font is joined; a body-size manuscript title stops before the authors.

## Result

Both Reader folders (92 PDFs) rerun with `--force` and compared field by field with iteration 12's run: one record changed. `baggerly_prodcon.pdf` is now titled "Are the NCI/FDA Ovarian Proteomic Data Biased? A Reply to “Producers and Consumers”"; it stays unresolved with its near-miss note.

## Done when

- Test first, then the fix, one commit; `uv run pytest` and Ruff pass.
- The Reader folders rerun with `--force` into a separate folder: only `baggerly_prodcon.pdf` changes, to its full title.
- `design_decisions_and_rationale.md` (title guess paragraph) and the history table updated; `status: completed`.
