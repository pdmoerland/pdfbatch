---
title: Unpaywall by default
type: feat
status: completed
date: 2026-10-30
iteration: 16
---

# pdfbatch iteration 16: Unpaywall by default

Oct 30, 2026 · @Perry Moerland

## Why

Unpaywall was opt-in (`--unpaywall`) because it "needs a real e-mail address". But `--mailto` is required anyway, for the CrossRef and OpenAlex polite pools, and a request Unpaywall refuses is treated as no answer: the OA data then comes from OpenAlex, exactly as without the flag.

Measured on the 92 `_PDM/Reader` PDFs (76 with a DOI): Unpaywall answered for all 76, and for every one OA status, licence, version and free-copy link were identical to OpenAlex's; only `oa.source` changed. Where it matters is preprints: OpenAlex merges a preprint into its journal article, so pdfbatch ignores its record for a preprint DOI (iteration 7), leaving only the "preprint servers are green" rule, without licence and with a guessed version. Unpaywall answers for the exact DOI: for `Downloads/papers/Chen-2022-biorxiv.pdf`, `cc-by-nc-nd` and `acceptedVersion` instead of no licence and `submittedVersion`.

Cost: one request per DOI, cached for 30 days and throttled like the others.

## Change

- `--unpaywall` becomes `argparse.BooleanOptionalAction` with default on: `--no-unpaywall` turns it off; `--unpaywall` stays valid.
- The benchmark is unchanged: it scores DOIs, not OA, and never queried Unpaywall.

**Test:** `test_unpaywall_is_the_default`: without the flag the CLI passes the `--mailto` address as `unpaywall_email`; with `--no-unpaywall` it passes none.

## Done when

- Test first, then the change, one commit; `uv run pytest` and Ruff pass.
- README (example, options, OA section), `design_decisions_and_rationale.md` ("Open access") and `.env.example` updated; history table row 16; `status: completed`.
- `Downloads/papers` and the two Reader folders rerun with `--force`, now with Unpaywall.
