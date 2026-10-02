---
title: Harder PDFs — review records, title banners and scans
type: fix
status: completed
date: 2026-10-02
iteration: 5
---

# Iteration 5 — Harder PDFs

## Overview

Running pdfbatch on a real reading list (`_PDM/Reader/Transcription factors`) left some PDFs unresolved. Three of them each exposed a different gap, and each fix is general rather than a special case for one file.

| PDF | Symptom | Root cause | Fix | Commit |
|---|---|---|---|---|
| `berman_pnas02.pdf` (PNAS 2002) | unresolved | Footer DOI mangled by the font encoding (`orgcgidoi10.1073pnas…`). The title search then stopped at CrossRef, whose top 5 hits were all *"Faculty Opinions recommendation of …"* records, rightly rejected by validation. | Ask OpenAlex whenever CrossRef has no hit that **validates**, not only when it returns nothing. | `75f8ef1` |
| `keles_motif.pdf` (Berkeley working paper 2003) | unresolved | The largest text on page 1 is the series banner, not the title, so only the banner was searched. | Keep one title guess per font size (three largest) and search each until one validates. | `84f9cba` |
| ″ | `Article5–Article5` in the citation | OpenAlex encodes article numbers as first = last page = `"Article5"`. | Turn them into `article_number`; drop a last page equal to the first; APA writes "Article 5". | `84f9cba` |
| `liu_motif.pdf` (JSTOR scan, 1995) | unresolved | Image-only PDF: no text layer at all. | OCR page 1 when Tesseract is installed. | OCR fallback |

## OCR fallback

- **When:** page 1 has fewer than 40 characters of text. Only page 1 is read: it has the title, authors and, on JSTOR cover pages, a full citation. That is enough to identify the paper, and one page takes a second or two rather than a minute.
- **How:** `page.get_textpage_ocr(language="eng", dpi=300, full=True)` in PyMuPDF. It needs Tesseract installed and raises `RuntimeError` when it isn't; `_ocr` then returns `None` and nothing else changes.
- **One code path:** page-1 analysis (text, header/footer bands, title guesses) now works on a PyMuPDF *TextPage*, either the page's own or the OCR one. This replaced `page.get_text(…, clip=…)` with `TextPage.extractTextbox(…)` for the bands.
- **Notes on the record:**
  - with OCR: "Scanned PDF: page 1 was read by OCR."
  - without Tesseract: what to install, and to rerun with `--force`. The record is cached by hash, so a plain rerun would skip it.
- **No new options.** OCR switches itself on once Tesseract is present.

## Verification

- **Page-1 extraction**, before vs after the TextPage refactor, on 58 PDFs (5 fixtures, 44 in *Transcription factors*, 9 in *GRN_inference*):
  - Page text and title guesses: identical.
  - Header/footer bands: differ only at band edges. `extractTextbox` also takes characters that cross a band's edge, and keeps hyphens.
  - DOIs and arXiv IDs found in the bands: identical, except SCENIC+, whose header DOI is now found directly.
- **Full pipeline, old vs new reader**, on the 53 real PDFs: the same confidence, DOI and route for every one (48 high, 2 medium, 3 unresolved).
- **Tests:** 30 pass.
  - Berman: CrossRef returns only a review record and OpenAlex has the paper.
  - Keles: a banner set larger than the title.
  - Three OpenAlex page cases and an APA article number.
  - A scan read by "OCR". The test substitutes the textpage of the page the scan was rendered from, because tests cannot assume Tesseract.
  - A scan without Tesseract.
- **Real OCR:** verified once Tesseract 5.4 turned out to be installed but not on `PATH`. PyMuPDF only looks at `TESSDATA_PREFIX` and `PATH`, so `_tessdata()` now also checks the installers' standard folders. `liu_motif.pdf` (0.6 s OCR) → `10.1080/01621459.1995.10476622` and `Roth_AlignAce.pdf` (1.9 s) → `10.1038/nbt1098-939`, both high confidence. Roth's OCR title guesses are fragments, but CrossRef's search still finds it.

## All-caps titles

`thijs_thesis.pdf` (KU Leuven PhD thesis, 2003) set its title in capitals. The title filter required a lower-case letter, so it fell back to the smaller university header. The rule is now "at least three words of two or more letters". That accepts all-caps titles and still rejects short banners ("BMC Bioinformatics") and letter-spaced headers ("A R T I C L E S"). An unresolved record whose title is all capitals gets it in sentence case.

On the 58 PDFs, title guesses changed in 6. The thesis gets its real title first, two-word banners disappear, and one spaced author line is added as a harmless last guess. Resolution is unchanged for all 53 real PDFs.

## Remaining unresolved in that folder

- `thijs_thesis.pdf`: no CrossRef or OpenAlex record exists, so it stays unresolved, now with its correct title. Author and year must be added by hand.
