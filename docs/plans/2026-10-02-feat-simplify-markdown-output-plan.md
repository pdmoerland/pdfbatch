---
title: Leaner papers.md
type: feat
status: completed
date: 2026-10-02
iteration: 3
---

# Iteration 3 — Leaner Markdown output

## Overview

In practice `papers.md` carried too much information. Most of it either repeated the APA citation or is better read in the reference manager. This iteration cut each entry down to what you scan for. RIS and JSON are unchanged.

## Changes

### `844ac08` — simplify paper entries

| Before | After | Why |
|---|---|---|
| Summary table at the top (title, first author, year, journal, OA badge, code/data icons, file) | Removed | It duplicated the entries below and became unreadable with many papers. |
| Numbered **bold title heading** per paper, plus an HTML anchor for the table | Removed | The title is already in the APA citation. Without the table the anchor had no use. |
| Separate **Authors** line | Removed | Also in the citation. |
| **File** line near the end of an entry | **First line of every entry** | The file is what you have in hand. Entries now read: *which PDF → what it is*. |
| OA line with emoji badges (🟡 💎 🟠 🟤 🟢 🔒 ❔) | Plain text, e.g. `hybrid (cc-by, publishedVersion)` | The emoji rendered as rectangle boxes in the user's viewer. |
| Entries separated by headings | Separated by a horizontal rule `---` | Needed once the headings were gone. |

The helpers used only by the table and badges (`_OA_BADGE`, `_cell`, `_first_author`) were removed.

### `3d9e172` — leave out the abstract

- **Removed:** the collapsible `<details>` abstract.
- **Resolved line:** no longer mentions where the abstract came from, which would be meaningless without the abstract.
- **Still kept:** the abstract in RIS (`AB`, so EndNote and Zotero get it) and in JSON (`abstract`, `abstract_source`).

## Resulting entry

```markdown
**File:** [`doe2024_zebrafish.pdf`](file:///…/doe2024_zebrafish.pdf)

> Doe, J., Chen, W., & van der Berg, P. (2024). Single-cell atlas of … *Nature Methods*, *21*(6), 1012–1024. https://doi.org/10.1038/s41592-024-02201-0

**DOI:** [10.1038/s41592-024-02201-0](https://doi.org/10.1038/s41592-024-02201-0)  
**Other IDs:** PMID 38400001, PMC1100001  
**Open access:** hybrid (cc-by, publishedVersion) — [free copy](https://…)  
**Code:** [github.com/vanderberglab/zebraheart](https://github.com/vanderberglab/zebraheart)  
**Data:** [doi.org/10.5281/zenodo.1234567](https://doi.org/10.5281/zenodo.1234567)  
**Resolved:** high confidence via doi:page1-header → crossref → openalex → europepmc

---
```

## Not changed

- The page header (counts and LLM token use) and the "Needs review" list.
- The ⚠️ marker in front of notes. It may also render as a box in some viewers, so it is a candidate for the same treatment.

## Verification

The Markdown test now asserts:

- no summary table, title headings or Authors line;
- the first entry starts with `**File:**`;
- a plain-text OA status with no emoji;
- no "Abstract".
