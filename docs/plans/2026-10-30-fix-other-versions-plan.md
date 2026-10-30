---
title: Other versions named in the PDF, and near-miss title searches
type: fix
status: completed
date: 2026-10-30
iteration: 12
---

# pdfbatch iteration 12: other versions are noted, not cited

Oct 30, 2026 · @Perry Moerland

Two misidentifications from a real run (two reading lists, 92 PDFs), both breaking the rule that a record describes the PDF you hold.

## Fix 1: a DOI the PDF names as another version is not its own

**Case:** `conservation_tavazoie.pdf` (PLoS Comput Biol 2005). Page 1 prints its own DOI and, in a footnote, "A previous version of this article appeared as an Early Online Release … (DOI: 10.1371/journal.pcbi.0010069.eor)". Both DOIs resolve, both validate `high`, and CrossRef relates neither to the other. The footnote sits in the page-1 footer band (score 0.9), so the `.eor` DOI beat the article's own DOI (page 1, 0.8).

**Fix:** in `identifiers.py`, a DOI in the same sentence after a cue such as "previous version", "earlier version", "Early Online Release", "originally published" or "previously appeared" is not a candidate. `other_version_dois(pdf)` returns those DOIs, and the pipeline notes them ("Other version named in the PDF: …"), like the CrossRef preprint relations.

Deliberately *not* a cue: "preprint". bioRxiv stamps every page with "bioRxiv preprint doi: …", which is the PDF's own DOI.

**Test:** page-1 text with both DOIs → the `.eor` DOI is not a candidate, the article DOI is, and `other_version_dois` returns the `.eor` one.

## Fix 2: a title-search hit must be `high`

**Case:** `baggerly_prodcon.pdf`, the 2004 technical report "Are the NCI/FDA Ovarian Proteomic Data Biased? A Reply to 'Producers and Consumers'". It has no DOI; title search found the 2005 Cancer Informatics article "Bias, Randomization, and Ovarian Proteomic Data: A Reply to 'Producers and Consumers'": same authors, same subtitle, title score between 75 and 90, so `medium`, and applied.

**Why the rule changes for searches only:** for a DOI found in the PDF, `medium` mostly means hard-to-read text (OCR, a broken title line); the identifier is evidence in itself. For a search hit there is no such evidence, and a near-miss title more often means another version of the work (technical report, preprint, conference paper vs. journal article).

**Fix:** `_title_search` returns a hit only when it is `high`. The best `medium` hit is kept as a near miss: if nothing else identifies the PDF, it stays unresolved with the note "Possibly another version of this PDF: "<title>" (<link>); its title differs." Nothing from the near miss is merged. DOI lookups (tiers 1–2, and the DOI half of GROBID/LLM keys) keep accepting `medium`.

This changes a validation rule. There is no local gold set, so it is checked by rerunning the two reading lists and comparing: only `baggerly_prodcon.pdf` was a `medium` title-search result there.

**Test:** a PDF whose CrossRef search hit has the same authors and a nearly matching title → unresolved, with the near-miss note.

## Out of scope

The title guess for `baggerly_prodcon.pdf` keeps only the first line of a two-line title. Worth its own look.

## Result

Both reading lists (92 PDFs) rerun with `--force` into a separate folder and compared field by field with the earlier run: exactly two records changed. `conservation_tavazoie.pdf` now has `10.1371/journal.pcbi.0010069` (and the final title) with a note naming the `.eor` DOI; `baggerly_prodcon.pdf` is unresolved with the near-miss note.

## Done when

- One commit per fix, test first; `uv run pytest` and Ruff pass.
- `design_decisions_and_rationale.md` (scoring table, validation rules, preprints section) and the history table updated; `status: completed`.
- The two reading lists rerun with `--force` into a separate folder: `conservation_tavazoie.pdf` gets `10.1371/journal.pcbi.0010069`, `baggerly_prodcon.pdf` is unresolved with the note, and nothing else changes.
