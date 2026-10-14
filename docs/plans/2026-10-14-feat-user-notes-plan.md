---
title: Your own notes, in a file pdfbatch only reads
type: feat
status: completed
date: 2026-10-14
iteration: 8
---

# Iteration 8 — Your own notes

## Overview

Users want to keep notes about papers next to the generated overview. pdfbatch owns all three output files, though:
- `papers.md` and `papers.ris` are rewritten on every run;
- `papers.json` survives normal reruns, because known PDFs are reused, but is rebuilt with `--force`;
- a note added to a record's `notes` in the JSON would show up as a pdfbatch warning.

There was no place for a note that would last.

## Decision

A separate file the user owns, **`<out>/notes.md`**, that pdfbatch reads and never writes:

```markdown
## Chen-2022-biorxiv.pdf
Good benchmark of deconvolution methods; RCTD and cell2location come out best.

## doe2024_zebrafish.pdf
Compare their enhancer grammar with our Fig. 3.
```

- **Format:** one level-2 heading per paper, holding the PDF's file name (backticks allowed), then free text. Deeper headings (`###`) and lists belong to the note. Text before the first heading and empty notes are ignored.
- **Matching:** by file name, case-insensitively. A note whose name matches no PDF is reported on the command line (`notes.md: no PDF named 'gone.pdf' (renamed, or a typo?)`), so a rename or a typo can't silently orphan it.
- **Refreshed on every run** into a new `Record.user_notes` field. The JSON shows the current notes, and deleting a note from `notes.md` removes it from all outputs. With `--force` the notes are simply applied again.
- **Output:**
  - `papers.md`: a **Notes:** block directly under the citation, the first thing read after it, and separate from pdfbatch's ⚠️ warnings.
  - `papers.ris`: an `N1` of its own before pdfbatch's `N1`. Zotero imports each `N1` as a separate note, and EndNote adds it to the reference's notes. RIS has no line breaks within a field, so paragraphs become one line there.

### Considered and rejected

- **Editing `papers.md` and preserving the edits:** would need diffing generated against hand-written text. Fragile, and invisible when it fails.
- **Notes in `papers.json`:** lost with `--force`, and mixed with pdfbatch's own data.
- **Keying notes by SHA-256** (which survives renames): unreadable and untypable for a person. The file name is what you see. Renames are reported instead.

## Implementation

- **`writers.read_notes(path)`** → `{file name: note}`, about 15 lines.
- **`cli.main`** reads `<out>/notes.md` after processing and attaches each note by matching file names case-insensitively. Notes that match no PDF are logged.
- **`writers`:** `_md_entry` adds the **Notes:** block, and `ris_entry` adds the extra `N1`.

Records without a note produce exactly the same Markdown and RIS as before.

## Verification

- **`test_read_notes`:** parsing, with backticks, `###` inside a note, and the preamble and empty notes ignored.
- **`test_notes_survive_reruns`**, end to end through the CLI with mocked APIs:
  - a note matched in different case appears in `papers.md` and as its own `N1` in `papers.ris`, after a `--force` run;
  - an unknown file name is reported;
  - emptying `notes.md` removes the note on the next run.
- **Suite:** 51 tests pass.
