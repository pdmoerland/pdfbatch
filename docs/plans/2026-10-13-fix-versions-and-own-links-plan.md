---
title: The right version, and only the paper's own links
type: fix
status: completed
date: 2026-10-13
iteration: 7
---

# Iteration 7 — The right version, and only the paper's own links

## Overview

Iteration 6 made sure a record is applied only when the PDF confirms it. This iteration applies the same standard, specificity over sensitivity, to what is *attached* to a confirmed record. That covers the bibliographic fields filled in from other sources and the code and data links. All three fixes came from PDFs in the user's library.

| Commit | PDF | Symptom | Fix |
|---|---|---|---|
| `92042ef` | `Hu-2025-preprint-STARNet.pdf` | Two code links: `…/DBinary/STARN` and `…/DBinary/STARNet`. | A text URL that is a prefix of a hyperlink is dropped. |
| `2671d54` | `Chen-2022-biorxiv.pdf` | The bioRxiv preprint was cited with *23*(4), the volume and issue of the published article. | An OpenAlex record is used only if its DOI is the DOI asked for. |
| `60cfd1b` | `Chen-2022-biorxiv.pdf` and others | "Code" listed the eight methods the paper benchmarks, two of them cut short (`SPOTli`, `Stereoscop`). | Only links the paper offers as its own. |

## Truncated hyperlinks (`92042ef`)

bioRxiv prints line numbers in the margin. A wrapped URL then comes out of text extraction as `https://github.com/DBinary/STARN` · `1472` · `et, and the detailed…`, which cannot be stitched back from the text alone. The PDF's link annotation has the whole URL.

**Rule:** annotations are authoritative. A text URL that is a strict prefix of an annotated URL, and is not annotated itself, is a cut-off copy and is dropped. Its position in the text still locates the hyperlink, which the own-links rule below needs.

## Another version's record (`2671d54`)

OpenAlex merges a preprint and its published version into one work. Asked for `10.1101/2022.02.20.481171`, it returns the record of `10.1093/bib/bbac245` (*Briefings in Bioinformatics*). Enrichment fills gaps only, but the bioRxiv record *has* gaps exactly where a journal article has values, so it took the article's volume 23, issue 4, ISSN 1467-5463, PMID 35753702, and an OA licence and link.

**Rule:** `apis.openalex_work` returns a record only if its DOI is the DOI asked for; otherwise `None`. That covers both uses: enrichment, and the fallback for DOIs that CrossRef doesn't know. Europe PMC needed no change, as it returns its own preprint record (`PPR459893`).

The entry is now a plain bioRxiv citation, matching what bioRxiv's own citation export gives, with the published version in a note. `--prefer published` cites that version instead.

## Only the paper's own links (`60cfd1b`)

Chen's links came from Table 1, the methods it compares: each row has a tool's URL and its citation (`dev/AdRoit` · `[16]`). The table sits after the References section and after a "Methods" heading, so "drop links inside References" didn't apply.

**First attempt, measured and rejected:** reusing the existing label trigger phrases ("Code is available at", "Data availability") as the claim signal. Across 102 PDFs it would have dropped genuine own links: "GLUE is publicly accessible at", "SCENIC+ and the code … is available at", "The source code of STARNet is available on GitHub".

**Rule:** a link is listed only when the paper *offers* it (`links._claimed`):
- **Where to look:** only the URL's own sentence, from the last ". " or blank line within 300 characters.
- **Claims:** availability, available, accessible, deposited, archived, released, hosted, "can be found / accessed / downloaded / obtained".
- **Uses:** "downloaded / obtained / retrieved / taken / adapted from", "provided at / by / in", "based on", "implemented in".
- **Nearest statement wins:** "…is available at X and deposited in Zenodo at Y" claims both; "…was downloaded from X" claims nothing.
- **Tags:** a tag directly before the URL ("Code: https://…") also counts as a claim.
- **No position, no claim:** a hyperlink whose place in the text cannot be found has no sentence to check, and is dropped.
- **References:** links inside a References section are dropped, as before.

**Bugs found on the way:**
- **Invented repositories.** The line-break repair joined a URL ending in "." with the next line, so `…/gao-lab/GLUE.` + `Results` (a section heading) and `…/GLUE.` + `Nature Biotechnology | VOL 40` (a page footer) became repositories that don't exist. So did `…/uci-cbcl/SAILERX.` + `SUPPLEMENTARY DATA`. After a dot it now joins only when the next line continues in lower case (`https://github.` + `com/gao-lab/GLUE`, which occurs in the same paper). An interim rule, "a hyperlink and a text URL are the same link if either is a prefix of the other", also fixed SAILERX, but would merge distinct repositories such as `repo` and `repo-extras`. It was replaced by this fix at the root.
- **Zero-width spaces.** Genome Biology prints URLs with zero-width spaces as break points (`https://​zenodo.​org/…`), so they could not be found in the text. They are now removed first.

## Verification

All 102 PDFs (two reading lists, a project literature folder and a folder of recent downloads), old code against new for each commit:

- **`92042ef`:** only STARNet changes, from two code links to one.
- **`2671d54`:** only Chen changes. It loses volume, issue, ISSN, PMID and the OA licence and link that belonged to the journal article.
- **`60cfd1b`:** no citation changes; link lists change in 7 PDFs.
  - **Removed, other people's tools:** Chen's 5 benchmarked tools; the Badia i Mompel review's 5 tools from tables; SpaGRN's 5 key-resources entries; in SCENIC+, cisTopic ("downloaded from") and libpointmatcher ("based on"); in GLUE, MMD-MA ("script provided at") and LIGER.
  - **Removed, broken or cited:** the invented `GLUE.Results`, `GLUE.Nature` and `SAILERX.SUPPLEMENTARY`, and a Zenodo record in KEGNI's reference list.
  - **Kept:** every own link (SCENIC+, GLUE, SpaGRN, KEGNI, SVGRN, SAILERX, STARNet).
  - **Newly listed:** `pycistarget` and the authors' modified `cluster-buster` branch for SCENIC+. Both are offered as part of the work, and now fit within the five-per-kind limit.
- **Tests:** 49 pass. New tests cover the cut-off hyperlink, the merged OpenAlex record, own versus compared links, "Availability and implementation:", and line-break repair at sentence ends. Each fails without its fix.

## Trade-off

An own link printed without any availability wording, such as a bare URL in a figure caption, is no longer listed. In the 102 PDFs this did not occur.
