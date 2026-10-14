# Design decisions & rationale

Long-form explanations of *why* pdfbatch is shaped the way it is. The [README](README.md) says how to install and run it. Use this file when its behaviour surprises you. How the code got here is told iteration by iteration in [`docs/plans/`](docs/plans/), listed under [Implementation history](#implementation-history).

## Contents

- [The problem](#the-problem)
- [Identify first, then ask structured APIs](#identify-first-then-ask-structured-apis)
- [Identification tiers, cheapest first](#identification-tiers-cheapest-first)
- [Validation: does this record describe *this* PDF?](#validation-does-this-record-describe-this-pdf)
- [The LLM only proposes a search key](#the-llm-only-proposes-a-search-key)
- [Enrichment and field precedence](#enrichment-and-field-precedence)
- [Open access](#open-access)
- [Preprints and published versions](#preprints-and-published-versions)
- [Code & data links](#code--data-links)
- [Outputs: JSON is the truth, RIS and Markdown are views](#outputs-json-is-the-truth-ris-and-markdown-are-views)
- [Incremental reruns](#incremental-reruns)
- [Being a polite API client](#being-a-polite-api-client)
- [Dependencies](#dependencies)
- [Code shape](#code-shape)
- [Implementation history](#implementation-history)

---

## The problem

You have a folder of PDFs and want them in EndNote or Zotero with correct, structured metadata: split author names, journal, volume, issue, pages, DOI. You also want to know which papers are open access and whether they share code or data. Reference managers' own "retrieve metadata from PDF" features are unreliable. Most often they pick up a DOI from the reference list.

pdfbatch started as a spin-off of a Slack bot that resolved paper *links* DOI-first. It was then cut loose as a standalone tool. Its central bet is the same: **find an identifier, then let authoritative APIs supply the metadata.** Scraping and LLMs are kept to the margins.

---

## Identify first, then ask structured APIs

For each PDF the pipeline is: **identify → resolve → validate → enrich → find links** ([`pipeline.py`](pdfbatch/pipeline.py)).

- **Resolve.** A DOI goes to CrossRef first. If CrossRef doesn't know it, it goes to OpenAlex, which also covers DataCite DOIs (arXiv, Zenodo). An arXiv ID goes to the arXiv API.
- **Enrich.** OpenAlex, Europe PMC and optionally Unpaywall fill the gaps.

**Why.** Structured APIs return stable, typed fields: given and family names, volume, issue, pages, ISSN, item type and preprint relations. A reference manager needs exactly these, and text extraction cannot supply them reliably. CrossRef alone covers the vast majority of journal articles.

---

## Identification tiers, cheapest first

Each tier runs only when the previous one produced nothing that validates ([`identifiers.py`](pdfbatch/identifiers.py), `_fallbacks` in [`pipeline.py`](pdfbatch/pipeline.py)).

| Tier | Source | Cost |
|---|---|---|
| 1 | XMP (`prism:doi`, `dc:identifier`, …) and the PDF Info dict | 0 |
| 2 | DOI / arXiv regex by position: page-1 header/footer and margins → page 1 → page 2 → tail of the last page | 0 |
| 3 | Title guesses (the three largest fonts on page 1, then the Info title, then the opening lines) → CrossRef `query.bibliographic` → OpenAlex search | 0 tokens |
| 4 | GROBID header parse (`--grobid URL`), then DOI or title lookup | 0 tokens |
| 5 | LLM search key from page 1 (`--llm anthropic\|ollama`) | ~1.5k tokens, rare |

**Position beats frequency.** The real hazard is a cited paper's DOI, so candidates are scored by *where* they appear:

| Where the DOI appears | Score |
|---|---|
| XMP | 1.0 |
| Info dict | 0.95 |
| Header/footer band | 0.9 |
| Page 1 | 0.8, dropping 0.1 for each earlier DOI on the page |
| Page 2 | 0.5 |
| End of the last page | 0.3, and only if that page has no References heading |

Text after a References heading is never searched, and a DOI seen in several places gets a small bonus. DOIs of data repositories (Zenodo, figshare, Dryad, OSF, Synapse) are never taken as the article's own.

**The title guess** joins the lines set in the largest font in the top 60% of page 1, keeping the first vertically contiguous run. The result must be 15–350 characters and must not look like a journal banner or "Research article". It is cheap and good enough for journal PDFs; GROBID and the LLM catch the rest.

**Scans.** A PDF whose page 1 has (almost) no text is OCR'd, page 1 only, when Tesseract is installed (`_ocr` in [`pdf_reader.py`](pdfbatch/pdf_reader.py), via PyMuPDF, English, 300 dpi). Page 1 carries what identification needs: title, authors and often a citation block (JSTOR cover pages), and one page costs a second or two rather than a minute. The OCR text then goes through the same tiers and the same validation as any other text, and the record notes that OCR was used. Only Tesseract's language files are needed, because PyMuPDF has its own OCR engine. They are found via `TESSDATA_PREFIX`, a `tesseract` on `PATH`, or the installers' standard folders, because the Windows installer does not add Tesseract to `PATH`. Without Tesseract nothing breaks: the PDF is flagged with what to install. Page 1 is analysed from a PyMuPDF *TextPage* — the page's own or the OCR one — so both paths share one code path.

**Laziness is structural.** Tiers 3–5 are a generator, and `identify` takes the first good hit with `next(...)`. So a GROBID server or an LLM is never contacted for a PDF that a cheaper tier already identified.

---

## Validation: does this record describe *this* PDF?

Every API hit is checked against the PDF text before it is accepted (`validate`).

1. **Title.** The API title is fuzzy-matched against the first three pages plus the Info title, using `rapidfuzz.partial_ratio`. Both sides are normalised: accents, ligatures, hyphenated line breaks and punctuation are removed.
2. **Authors.** The family names of up to the first five authors must appear as whole words in that text. Digits count as word boundaries, because affiliation numbers stick to names in extracted text (`Segal1`). Source records have typos and truncations ("Patterso" for Patterson), so names of 8–11 letters may differ by one character and names of 12 or more by two. Shorter names must match exactly, because one edit away from a short name is often an ordinary word: "Chen" is "then", and a reviewer called "States" is "stages".

| Confidence | Rule |
|---|---|
| `high` | title ≥ 90 **and** the authors found |
| `medium` | title ≥ 75 **and** the authors found |
| `unresolved` | nothing confirmed; PDF-derived title and Info-dict authors only |

**Specificity over sensitivity.** A title alone never confirms a match: a record is applied only when the PDF shows its title *and* its authors. An API hit that falls short is not used at all, not even as a flagged guess. A PDF left unresolved costs a minute by hand; a wrong record imported into your library may never be noticed.

**How the result is used:**
- **Accepted:** `high` and `medium`.
- **Review flag:** `unresolved` records are listed under "Needs review" in Markdown and tagged `pdfbatch: needs review` in RIS, so you can filter them in your reference manager.
- **Title-search guard:** search hits must also resemble the query (`token_set_ratio` ≥ 85) and must not be a mere fragment of it: the hit's title needs at least 60% of the query's length. Otherwise an encyclopedia entry named after a method in the title ("Surface-Enhanced Laser Desorption/Ionization…") shares the words without being the paper. A *longer* hit is fine, because OCR'd queries can be fragments themselves.
- **"The authors":** for a DOI or arXiv ID found in the PDF, at least one of the first five authors, because the identifier is evidence in itself. For a title-search hit, *every* author: a generic title ("Model Assessment and Selection") belongs to many works, such as a slide deck and a textbook chapter. A record without authors can never pass.
- **Short titles prove nothing:** an API title of fewer than three words ("32", "NCI & FDA") occurs in almost any text, so it never validates a match.
- **Garbled text is no text:** a page 1 whose text is more than 1% control characters comes from a font without a character map (common in older LaTeX PDFs). It is OCR'd like a scan.
- **Last query, the opening lines:** after the font-based title guesses and the Info title, the first two substantial lines of page 1 are searched. In reading order the title usually comes first, and this rescues PDFs whose font sizes mislead, OCR output in particular.

**Why.** Without validation, the first DOI on page 1 is often a reference ("Earlier atlases (doi:…) lacked …"). One cheap fuzzy match catches this.

---

## The LLM only proposes a search key

The LLM tier is off by default ([`llm.py`](pdfbatch/llm.py)).

- **Input:** at most 6,000 characters of page-1 text.
- **Output:** JSON with `title`, `first_author_surname`, `year` and `doi`. That key goes back through the normal DOI lookup or title search, and then through validation.
- **Never metadata:** **the LLM never writes metadata into the RIS file.** A hallucinated title simply fails validation.
- **Backends:**
  - **Anthropic:** default model `claude-haiku-4-5`.
  - **Ollama:** default model `qwen3:8b`, using native `/api/chat` with a JSON-schema `format` and `think: false`.
- **Cost reporting:** token use is tallied and printed, and appears at the top of `papers.md`.

---

## Enrichment and field precedence

- **Merge rule:** the record that identified the paper is merged with *override*. Later sources only fill fields that are still empty (`_merge`).
- **Same version only:** OpenAlex merges a preprint and its published version into one work, so looked up by a bioRxiv DOI it can return the journal article (`10.1101/2022.02.20.481171` → `10.1093/bib/bbac245`). Its volume, issue, ISSN, PMID and OA data belong to the other version. An OpenAlex record is therefore used only if its DOI is the DOI asked for.
- **Abstract priority:** CrossRef → Europe PMC (curated) → OpenAlex (rebuilt from an inverted index, occasionally truncated) → a slice of the PDF's own page 1–2 text. The winner is recorded in `abstract_source` in the JSON.
- **Europe PMC** is queried only when the abstract or PMID is still missing. It adds PMID, PMCID and keywords for biomedical papers.
- **CrossRef licences** (version-of-record or unspecified) fill the OA licence when the OA sources don't give one.

The abstract goes to RIS (`AB`) and JSON. It was deliberately taken out of `papers.md` to keep that file short (see [iteration 3](docs/plans/2026-10-02-feat-simplify-markdown-output-plan.md)).

---

## Open access

OA status is one of `gold`, `diamond`, `hybrid`, `bronze`, `green` or `closed`, with licence, version and a free-copy URL where known.

- **Unpaywall** (`--unpaywall`) wins when it returns a status. It needs a real e-mail address, so it is opt-in. If Unpaywall has no licence, the one already known is kept.
- **OpenAlex** otherwise.
- **The preprint rule:** arXiv, bioRxiv/medRxiv, Research Square, Preprints.org, PsyArXiv, OSF Preprints, TechRxiv, Authorea and SSRN DOIs are green OA by definition (source `rule`).
- **No data:** when nothing is known for a DOI, a note says so rather than guessing.

A `closed` paper whose PDF you hold came through a subscription. The flag is there so you don't redistribute it.

---

## Preprints and published versions

CrossRef's `is-preprint-of` / `has-preprint` relations and arXiv's `<arxiv:doi>` link the versions of a paper.

- **Default (`--prefer held`):** the record describes the version you hold, with a note pointing to the other version.
- **`--prefer published`:** the published record replaces the bibliographic fields. Journal-specific fields are cleared first, so no arXiv leftovers remain. The preprint becomes `related_doi`, and the OA data is reset so that enrichment fetches the published version's status.

**Why default to the held version.** The PDF is what you will annotate and cite from. Silently citing a different version would be surprising.

---

## Code & data links

[`links.py`](pdfbatch/links.py) finds the paper's *own* code and data:

1. **Sources:** the full text, plus the PDF's link annotations. Annotations survive line-wrapped URLs; the text is also repaired: zero-width spaces that some publishers put inside printed URLs are removed, and URLs broken at line ends are re-joined. After a dot only when the next line continues in lower case (`github.` · `com/…`); a capital after a dot starts a new sentence or heading (`…/GLUE.` · `Results`), and joining it would invent a repository. Where the text has only a cut-off prefix of an annotated URL, for example when a preprint's margin line number splits it (`…/STARN` · `1472` · `et`), the annotation wins and the prefix is dropped.
2. **Recognised platforms:** GitHub, GitLab, Bitbucket, Codeberg (code); Hugging Face, Zenodo, figshare, Dryad, OSF, Synapse (data). Each URL is canonicalised. For example, `github.com/org/repo/tree/main/src` becomes `github.com/org/repo`, and `zenodo.org/records/N` becomes its DOI.
3. **Site pages:** reserved first path segments (`github.com/features`, `huggingface.co/docs`) are rejected as site pages.
4. **Only links the paper offers as its own.** In the URL's own sentence, the statement nearest to it must say the paper *offers* it ("is available at", "publicly accessible at", "deposited in Zenodo at", "has been archived") or a tag must precede it ("Code: https://…"). Statements of *use* ("downloaded from", "script provided at", "based on", "implemented in") and plain mentions don't count. This drops the tools a paper compares or relies on, which are typically listed in tables (Chen et al.'s benchmark table of eight methods, a review's tool tables, a key-resources table), and keeps its own code and data. Links inside a References section are dropped too; that section runs from the heading to the next Methods, Data availability, Acknowledgements, … heading.
5. **Blocklist:** a short list of framework repos that many papers cite (PyTorch, TensorFlow, transformers, NumPy, …) and arXiv/ar5iv site chrome.
6. **`--check-links`:** HEAD-checks platforms where content can disappear. DOI-registered platforms are permanent by contract and skipped. A 429 counts as alive (rate-limited, not dead). Results are cached for 30 days.
7. **Labels:** a label comes from a tag in front of the URL ("Weights: https://…") or a trigger phrase up to 200 characters before it ("Data are available at"). Labels that say nothing beyond the link's kind or platform ("Code", "GitHub", "Zenodo") are dropped.
8. **Limit:** at most five links per kind.

---

## Outputs: JSON is the truth, RIS and Markdown are views

Every run writes three files to `<input>/_metadata/` ([`writers.py`](pdfbatch/writers.py)).

### `papers.json`

The full records, and the only file that is read back.

### `papers.ris`

For import into EndNote or Zotero.

- **Encoding:** UTF-8 with CRLF line endings. `--bom` adds a byte-order mark for old EndNote versions.
- **Fields:**
  - **Authors:** one `AU  - Family, Given` per author.
  - **Date:** `DA` as `YYYY/MM/DD/`.
  - **Pages:** `SP`/`EP`. Article numbers go in `SP` and `M1`, because EndNote reads `M1` as the article number.
- **Preprints:** exported as `JOUR` with the server as the journal, because that imports cleanly everywhere. `--preprint-ris-type UNPB` is available.
- **`KW` keywords:** for OA status, licence, *Has code* / *Has data* and the review flag. They become **tags** in Zotero, so they are filterable.
- **`N1` note:** collects OA details, links, warnings and the resolution route.
- **`L1` attachment:** a `file://` URI by default (`--attach path|none` to change), so the PDF is attached on import.
- **Sorting:** by first author, year, title.

### `papers.md`

An overview for humans, kept deliberately lean:

- a one-line summary;
- a "Needs review" list;
- per paper:
  - the **File** line first, because the file is what you have in hand;
  - the APA 7 citation;
  - your own notes, if any (see below);
  - DOI and other IDs;
  - an open-access line in plain text, with no emoji, since those render as boxes in some viewers;
  - code and data links;
  - how the paper was resolved;
  - any warnings.

There is no summary table, no title heading, no separate author list and no abstract. The citation already carries title and authors.

### Your notes: a file you own

Notes about a paper live in `notes.md` beside the outputs, one `## <file name>` heading per paper. pdfbatch reads it on every run and never writes it. Every output file is pdfbatch's to regenerate (`papers.md` and `papers.ris` on every run, `papers.json` with `--force`), so a note kept in any of them would sooner or later be overwritten. A file the user owns separates the two cleanly.

- **Keyed by file name**, which is what you see and type; renamed PDFs are recognised by hash, but their notes need the new name. A note that matches no PDF is reported, so a rename or a typo can't silently orphan it.
- **Refreshed on every run** into `Record.user_notes`: the JSON shows the current notes, and a note deleted from `notes.md` disappears from the output.
- **Separate from pdfbatch's warnings:** in Markdown a *Notes* block under the citation, in RIS an `N1` of its own (Zotero imports each `N1` as a separate note).

### APA formatting

APA formatting works on structured `Author` objects:

- **People:** a family name plus initials.
- **Organisations:** kept as written.
- **Long author lists:** more than 20 authors gives the first 19, an ellipsis and the last.
- **Titles:** sentence case, but acronyms and mixed-case words (`DNA`, `iPSC`) are kept.

---

## Incremental reruns

Records are keyed by the PDF's SHA-256 ([`cli.py`](pdfbatch/cli.py)).

- **Reruns:** only new files are processed. A renamed or moved file is recognised and its `pdf_path` updated.
- **`--force`:** redoes everything.
- **Failures:** a PDF that cannot be opened, or a worker that crashes, gets a record with a note, and the batch continues. Those records carry no hash, so they are retried on the next run.

---

## Being a polite API client

All HTTP goes through one shared `Http` instance ([`apis.py`](pdfbatch/apis.py)):

- **User-Agent** with `mailto:`. This gets CrossRef and OpenAlex "polite pool" treatment, which is why `--mailto` / `CONTACT_MAILTO` is required.
- **Throttling** per host: 0.12 s between requests in general, and 3 s for arXiv, as its API terms ask.
- **Retries** on 429/5xx with exponential backoff, honouring `Retry-After`.
- **A 30-day disk cache** in `.pdfbatch_cache/`. 404s are cached too, so reruns and repeated lookups are nearly free.

PDFs are processed by four worker threads by default (`--workers`). The work is network-bound, and the shared throttle keeps parallelism polite.

---

## Dependencies

Five runtime dependencies: `requests`, `diskcache`, `python-dotenv`, `pymupdf` and `rapidfuzz`. There is no LLM SDK; both LLM backends are plain HTTP.

**PyMuPDF is AGPL.** It was chosen for one-pass access to text, font sizes (needed for the title guess), clipping (header and margin bands), XMP and link annotations. If AGPL matters for redistribution, only [`pdf_reader.py`](pdfbatch/pdf_reader.py) needs to be swapped, for pypdf plus pdfplumber.

---

## Code shape

The package has ten modules, about 1,900 lines, and one data flow:

```
cli ──► pipeline.process_pdf ──► pdf_reader.read_pdf
                              ├► identifiers.find_candidates
                              ├► apis.*            (+ llm, GROBID)
                              └► links.find_links
    ──► writers (json, ris, md)
```

Principles applied in [iteration 4](docs/plans/2026-10-02-refactor-minimal-elegant-code-plan.md):

- **One definition per idea:** "needs review", the OA rule, URL canonicalisation and the DOI-then-title lookup each exist once.
- **No knobs nobody turns:** settings live in `Settings` only if the CLI can set them; fixed policy lives in module constants (`MAX_PER_KIND`, `BLOCKLIST`).
- **No code for impossible cases:** required dependencies are imported directly, without `try: import` guards.
- **Refactors must not change output:** Markdown and RIS for the test fixtures are compared byte-for-byte before and after.

---

## Implementation history

| # | Plan | Commit(s) |
|---|---|---|
| 1 | [pdfbatch inside the paperbot](docs/plans/2026-10-02-feat-pdfbatch-batch-pdf-metadata-plan.md) | (on the former `pdfbatch` branch; not in history) |
| 2 | [Standalone tool](docs/plans/2026-10-02-refactor-standalone-pdfbatch-plan.md) | `ec51861` |
| 3 | [Leaner Markdown output](docs/plans/2026-10-02-feat-simplify-markdown-output-plan.md) | `844ac08`, `3d9e172` |
| 4 | [Minimal, elegant code](docs/plans/2026-10-02-refactor-minimal-elegant-code-plan.md) | `df2dcc4` |
| 5 | [Harder PDFs: review records, banners, scans](docs/plans/2026-10-02-fix-harder-pdfs-plan.md) | `75f8ef1` … `a17cf74` |
| 6 | [Specificity over sensitivity](docs/plans/2026-10-02-refactor-specificity-plan.md) | `4e42d8a` |
| 7 | [The right version, and only the paper's own links](docs/plans/2026-10-13-fix-versions-and-own-links-plan.md) | `92042ef`, `2671d54`, `60cfd1b` |
| 8 | [Your own notes](docs/plans/2026-10-14-feat-user-notes-plan.md) | the notes commit |
