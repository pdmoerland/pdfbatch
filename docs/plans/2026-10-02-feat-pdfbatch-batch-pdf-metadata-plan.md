---
title: pdfbatch — a folder of PDFs to Markdown + RIS + JSON, built on the paperbot
type: feat
status: completed
date: 2026-10-02
iteration: 1
---

# Iteration 1 — pdfbatch inside the paperbot

## Overview

This iteration added a batch tool, `pdf_batch.py` plus a `pdfbatch/` package, to the Slack paperbot repository. It turns a directory of PDFs into a Markdown overview, a RIS file for EndNote and Zotero, and a JSON source of truth. It reused the bot's DOI-first idea and several of its modules rather than starting from scratch.

## Problem statement

The bot resolves paper *links*. Here the input is the PDF itself, which raises three problems the bot never had:

- **Finding the identifier:** the PDF's own DOI has to be found in its text without picking up a cited paper's DOI.
- **Richer fields:** a reference manager needs structured authors, volume, issue, pages, ISSN, item type and preprint relations. The bot's `PaperMetadata` flattened all of that for a Slack card.
- **Open access:** OA status matters, because a held PDF may be subscription-only.

## Approach

- **Identification tiers, cheapest first:**
  1. XMP / Info dict;
  2. a position-ranked DOI/arXiv regex (header and margins, page 1, page 2, last page; reference lists excluded);
  3. a largest-font title search on CrossRef or OpenAlex;
  4. GROBID (optional);
  5. an LLM search key (optional, off by default).
- **Validation:** every hit is fuzzy-matched (title, author surnames) against the PDF text. This gives confidence `high`, `medium`, `low` or `unresolved`; below `medium` is flagged for review.
- **New API clients (`pdfbatch/apis.py`):** CrossRef, OpenAlex, Europe PMC, arXiv and Unpaywall, cached on disk and throttled per host. The bot's resolvers could not be reused because they returned the flattened Slack model.
- **Open access:** Unpaywall (opt-in) ▸ OpenAlex ▸ a "preprint servers are green" rule.
- **Preprints:** `--prefer held|published` decides which version to cite.
- **Code & data links:** found in the PDF full text and link annotations. Links inside References are dropped.
- **Outputs:** JSON as the source of truth. Reruns are incremental by SHA-256.

## Reused from the paperbot

These were imported directly from the bot's `src/`:

| Module | Used for |
|---|---|
| `src/enrichers/patterns.py` | code/data URL patterns and normalisation |
| `src/enrichers/heuristic_labeler.py` | link labels |
| `src/enrichers/validator.py` | `--check-links` HEAD probes |
| `src/extractors/__init__.py::_DEFAULT_BLOCKLIST_URLS` | framework/infrastructure blocklist |
| `src/formatters/citation_formatter.py` | APA author formatting, sentence case |

## Packaging

- **Dependencies:** a `pdf` extra in the bot's `pyproject.toml` (`pymupdf`, `rapidfuzz`), with `pdfbatch` added to the wheel packages.
- **Running:** `uv run pdf_batch.py ~/papers --mailto you@uni.nl --unpaywall`.
- **Secrets:** `CONTACT_MAILTO` read from the bot's `config/.env`.

## Markdown layout at this point

- **Page:** a header with counts, then "Needs review", then a **summary table** (title, first author, year, journal, OA emoji badge, code/data icons, file).
- **Per paper:**
  - a numbered **bold title heading**;
  - the APA citation;
  - **Authors** line;
  - DOI and IDs;
  - **OA with emoji badge**;
  - code/data;
  - File;
  - Resolved;
  - a collapsible **abstract**.

## Testing

`tests/pdfbatch/` holds 15 offline tests. They generate synthetic PDFs with PyMuPDF and mock the APIs with real response shapes. The cases:

- a header DOI next to a decoy DOI cited on page 1;
- an arXiv margin stamp;
- a PDF with no DOI (title search);
- a bioRxiv DOI with a version suffix;
- an unresolvable PDF;
- exclusion of links inside References;
- RIS and Markdown structure.

## Limitations carried forward

- **Dependency on the bot:** the tool could not be installed or run without the bot's code and config. This was resolved in [iteration 2](2026-10-02-refactor-standalone-pdfbatch-plan.md).
- **Windows `--help` crash:** the help text contained `→`, which crashed on a Windows cp1252 console. Fixed in iteration 2.
