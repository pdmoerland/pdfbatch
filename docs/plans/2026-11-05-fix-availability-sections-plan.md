---
title: Author lists at the end, and tools a paper only used
type: fix
status: in-progress
date: 2026-11-05
iteration: 19
---

# Iteration 19: author lists at the end, and tools a paper only used

Nov 5, 2026 · @Perry Moerland

All fixes come from one Nature Cancer paper (`s43018-024-00784-3.pdf`, "The proteogenomic landscape of multiple myeloma…").

| Commit | Symptom | Fix |
|---|---|---|
| `8127639` | Unresolved, although its DOI is in the XMP: CrossRef's record failed validation. Page 1 only says "A list of authors and their affiliations appears at the end of the paper", so no author was found in the first three pages. | When page 1 says so, author names are looked for in the whole text. The title must still match the first pages. |
| `8127639` | Code link `github.com/broa-dinstitute/ssGSEA2.0`: the typesetter's hyphen at a line break was kept. | A text URL with one hyphen too many gives way to the hyperlink annotation, as a cut-off prefix already did. |
| `8127639` | PMCID shown as `11358022`. | OpenAlex's PMC URL ends in the bare number; the `PMC` prefix is added. |
| (this iteration) | Code link `github.com/broadinstitute/ssGSEA2.0`, an existing tool the paper used. Its Code availability section says no new code was written. | See below. |

## Why ssGSEA2.0 was taken as the paper's own

`_claimed` reads only the sentence *before* the URL and takes the nearest statement: "The ssGSEA implementation **available** on https://github.com/broadinstitute/ssGSEA2.0 **was used** to …". "available" counts as an offer; "was used", after the URL, is never read. And the paper's own statement about code, in its Code availability section, is not consulted at all.

## Changes

1. **An availability section speaks for the paper.** Nature, Cell (STAR Methods "Data and code availability"), PLOS and most biomedical journals have a Code / Data / Software / Resource availability section. When a paper has one for a kind (code or data) that either contains a URL or says no new code was written, links of that kind count only inside such a section, or behind a tag ("Code: https://…", as in Bioinformatics abstracts). A section with neither ("Code is available on request", "see Methods") changes nothing: the link may well be in Methods. Within a section the section itself is the offer: a link needs no "available at" before it, but a statement of use still rejects it ("downloaded from UniProt"). Every mention of a link in a section is judged, not only its first mention in the text, which may be in Methods.
2. **A use after the URL vetoes it.** When the rest of the URL's sentence says "was used", "were applied", "was employed" …, the link is a tool the paper used, unless the sentence also says it is the paper's own ("our", "we", "this study"): "Our code is available at … and was used to make all figures" stays.

Deliberately not done: treating attributive "X available at URL" as a description rather than an offer. "Our package, available at https://…, …" has the same grammar.

## How behaviour is checked

- Regression tests from this paper's sentences, plus the opposite cases: a code link in Methods with an availability section that only says "see Methods"; "Our code … was used to …".
- `find_links` for about 530 local PDFs (four reading-list folders plus the test folder), dumped before and after; every difference inspected by hand. Link finding reads only the PDF, so this needs no API calls.

## Result

85 tests, Ruff and mypy pass. `find_links` before and after on 531 local PDFs (only 7 have code or data links at all; most are older papers):

| PDF | Before | After |
|---|---|---|
| The Nature Cancer paper | `broadinstitute/ssGSEA2.0` | none: its Code availability says no new code was written |
| A Nature Communications paper (ENCORE) | `ENCORE_AUTOMATION`, `FSS-Navigator`, three Zenodo DOIs | also `ENCORE`, the template the paper is about: offered in Code availability, but its Methods mention, the only one judged before, has no offer word |

The comparison first showed `ENCORE_AUTOMATION` lost: in Code availability its sentence has no "available" before the URL. That led to the rule that a section is itself the offer.

## Done when

- Tests, Ruff and mypy green; every before/after difference explained.
- Design doc, "Code & data links", updated; history table row 19; `status: completed`.
