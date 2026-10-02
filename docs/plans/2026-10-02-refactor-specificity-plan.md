---
title: Specificity over sensitivity, and a second minimalist pass
type: refactor
status: completed
date: 2026-10-02
iteration: 6
---

# Iteration 6 — Specificity over sensitivity

## Overview

A second "Beauty is our business" pass over the whole package, after iteration 5 had added OCR, DOI-less papers and stricter validation. The brief added a principle: **prefer specificity over sensitivity**. A wrong record imported into a reference library may never be noticed, while an unresolved PDF costs a minute by hand.

## Specificity

Two places still accepted records the PDF did not confirm:

| Before | After |
|---|---|
| A DOI hit whose title matched (≥ 90) was `medium` even when none of its authors appeared in the PDF. | A title alone never confirms a match. `high` = title ≥ 90 **and** authors; `medium` = title ≥ 75 **and** authors. |
| If nothing validated, the best `low` DOI hit was applied anyway, flagged "check it". | An unconfirmed hit is not used at all. The PDF stays `unresolved`, with only PDF-derived fields. |

"Authors" still means one of the first five for an identifier found in the PDF, and all of them for a title-search hit. As a result:
- **No stored "low":** it only exists inside validation, and a record is now `high`, `medium` or `unresolved`.
- **Simpler review rule:** `needs_review` is simply `confidence == "unresolved"`.
- **Clearer note:** the unresolved note now says what failed: "No record could be confirmed by title and authors".

## Elegance

- **`validate`:** one rule ("authors found" and a title score) instead of three interleaved conditions. Short titles score 0 rather than being a separate early return.
- **`identify`:** `_best_candidate(...) or next(good fallback)`. The low-confidence branch and `itertools.chain` are gone.
- **`_lookup` (GROBID, LLM):** returns `(fields, confidence)`. The `doi`/`title` label it used to return duplicated the route, where `crossref` vs `crossref-search` already says it. The routes are now just `grobid` and `llm-key`.
- **`enrich`:** flattened; each source states its own precondition instead of sitting in one big `if`.
- **`Record.item_type`:** defaults to `None` instead of `"journal-article"`, which removes a special case from `_merge`. RIS still exports unknown types as `JOUR`.
- **`apis.crossref_to_fields`:** four search loops (date, related version, ISSN, licence) became one `next(...)` each.
- **`pdf_reader._title_guesses`:** the same algorithm in half the temporaries. One `functools` import instead of two.
- **Markdown "Needs review":** no longer repeats "unresolved" before every note.

## Verification

- **Your library:** all 100 PDFs (both Reader folders and *GRN_inference*), full records before and after. Confidence, APA and RIS are identical for every PDF apart from the reworded note: 92 high, 1 medium, 7 unresolved. Every accepted match was already confirmed by title and authors, so the stricter rule costs nothing here.
- **Fixtures:** Markdown, RIS and JSON under three settings (default, `--unpaywall`, `--prefer published`). The differences are the reworded note and `item_type: null` for the unresolved fixture.
- **Title guesses:** identical on 58 PDFs after the `_title_guesses` rewrite.
- **Tests:** 45 pass. `test_a_title_alone_confirms_nothing` replaces the test that asserted the old title-only `medium`.
