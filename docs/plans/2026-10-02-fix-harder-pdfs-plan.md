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
- **One page at a time.** Leptonica, Tesseract's image library, fails when two worker threads OCR at once (`Attempt to use Leptonica from 2 threads at once!`, raised as a MuPDF `FzErrorBase`, not a `RuntimeError`). A lock serialises OCR, about 1–2 s per scan, while everything else stays parallel, and the guard also catches MuPDF errors, so a failed OCR never turns into "Could not open PDF".

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

## Papers without a DOI

Three *Cancer Research* papers from 2001–2003 (`_PDM/Reader/Markers`: `quakenbush_cancer.pdf`, `rogers_renal_cancer_seldi.pdf`, `wright_serum.pdf`) were unresolved. The journal registered no DOIs then, so CrossRef doesn't know them. OpenAlex and Europe PMC do.

| Problem | Fix |
|---|---|
| The title guess ended in a superscript footnote marker (`… Microarray 1`). The stray digit alone made OpenAlex's search miss. | Title guesses leave out spans set below 70% of the line's largest size. |
| OpenAlex's record has PubMed as its "source", so the journal became *PubMed*. | A repository source is not a journal. |
| No DOI, so no link. | Link PubMed when there's a PMID. No OpenAlex links: they are not citation links. |
| Enrichment ran only for DOIs. | Europe PMC is also queried by PMID, and also when the journal is missing. It now supplies journal, ISSN, volume, issue and pages, filling gaps only. |
| NLM journal titles are in sentence case ("Cancer research"). | The journal name comes from the ISSN via OpenAlex sources ("Cancer Research"). |
| PubMed-style abbreviated pages (`7792–7`). | One `_page_fields` helper for all three sources expands them, drops a last page equal to the first, and recognises article numbers written as pages (`Article5`, which Europe PMC uses too). |
| An all-caps family name from OpenAlex (`EARLE-HUGHES`). | `Author.from_display` puts it in normal case. |

Verified on 100 PDFs (both Reader folders and *GRN_inference*), comparing APA citations from the old and new code:
- the three papers go from unresolved to high;
- `ringner-bcr.pdf` gets `23–26` instead of `23–6`;
- two ranges like `2869–2869` become `2869`;
- nothing else changes.

Overall: 87 high, 7 medium, 6 unresolved.

## Wrong matches at medium confidence

Comparing citations across 100 PDFs showed three records that were confidently wrong. They were accepted at `medium`, so they didn't appear under Needs review, which is worse than unresolved.

| PDF | Wrong match | Why it got through |
|---|---|---|
| `cazares_seldi.pdf` | *Encyclopedia of Cancer* entry "Surface-Enhanced Laser Desorption/Ionization Time of Flight Mass Spectrometry" | The phrase is in the paper's abstract, so the title scored 100. With no authors to contradict it, that gave medium, and OpenAlex was never asked. |
| `baggerly_prodcon.pdf` | *Oncology Times* item "NCI & FDA" | A two-word title is found in almost any text. |
| `freitas_shrink.pdf` | Book chapter "32" | The PDF's text layer is garbage (a font without a character map), and it contains a "32". |

Measured on the 51 title-search hits: every correct hit's title is at least 97% as long as the query, or longer (OCR fragments). The wrong ones are at 18%, 33% and 45%. Page-1 control characters: 300 in `freitas_shrink.pdf`, 0 in the 99 others.

Fixes:
- Search hits must cover at least 60% of the query's length (`_resembles`).
- A title of fewer than three words cannot validate.
- Page 1 counts as unreadable when more than 1% of it is control characters, and is OCR'd (`unreadable`).
- The opening lines of page 1 are searched last (`PdfContent.opening`), because OCR'd font sizes mislead.

Result: Cazares → high, the real paper (via OpenAlex). Freitas → high, AISTATS 2003 (OCR, then the opening lines, then OpenAlex). Baggerly → medium, the published version in *Cancer Informatics* 2005 under a revised title. No other record changed. Overall: 89 high, 5 medium, 6 unresolved.

Known data issue: OpenAlex lists the AISTATS authors as Gustafson, Carbonetto, Thompson, de Freitas; the paper has Carbonetto, de Freitas, Gustafson, Thompson. Author order is taken from the source as is.

## Title-only matches

`benner_modelsel.pdf` is a slide deck titled "Model Assessment and Selection". The title search matched it to the chapter of that name in Hastie, Tibshirani & Friedman's *Elements of Statistical Learning*. The title was perfect and no author was found, which gave medium, so it was accepted.

Of all 100 PDFs, four accepted records had no author confirmed. Two of those were caused by a bug:

| PDF | Before | Cause | Now |
|---|---|---|---|
| `segal_imaging.pdf`, `segal_modules.pdf` | medium | Author check bug: affiliation numbers stick to names (`Segal1`), and `segal` has no word boundary between `l` and `1`. | high |
| `benner_modelsel.pdf` | medium: Hastie et al. chapter | A search hit accepted on its title alone. | unresolved (a presentation) |
| `srinivas_proteomics.pdf` | medium: Zhang (2011), "Proteomics *in* Cancer Biomarker Discovery" | The same. | high: Srinivas et al. (2002), *Clin Chem*, the real paper |

Fixes:
- Author names are matched with letters-only boundaries.
- Search hits need at least one author found in the PDF (`validate(..., need_author=True)`). DOI hits keep the old rule, because a DOI printed in the PDF is evidence in itself.

Overall: 92 high, 1 medium (Baggerly, the published version under a revised title), 7 unresolved.

### Stricter: all authors, tolerant of typos

Search hits now need **all** their authors in the PDF (`validate(..., all_authors=True)`).

- **Exact matching alone** rejected one correct paper: `kamvysselis_yeast_motif.pdf`, whose record misspells Patterson as "Patterso".
- **So names may differ slightly, scaled by length:** one character for 8–11 letters, two for 12 or more, checked word by word against the PDF's words (`_name_in`, rapidfuzz Levenshtein).
- **A first try was too loose:** allowing one character from 6 letters let a "Faculty Opinions" review of `berman_pnas02.pdf` through, because its reviewer "States" matched "stages". The thresholds were raised to 8 and 12.

Result: no record changes compared with the previous rule. Kamvysselis and Berman stay high, and the slides stay rejected.

## Remaining unresolved in that folder

- `thijs_thesis.pdf`: no CrossRef or OpenAlex record exists, so it stays unresolved, now with its correct title. Author and year must be added by hand.
