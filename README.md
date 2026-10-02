# pdfbatch: a folder of PDFs → `.md` + `.ris` (EndNote / Zotero)

`pdfbatch` turns a directory of papers into three files: a Markdown overview, a RIS file for import, and a JSON source of truth. It works DOI-first: identify the paper, then pull structured metadata from CrossRef, OpenAlex, Europe PMC, arXiv and Unpaywall. In the normal case no LLM is involved.

## Install and run

```bash
uv venv && uv pip install -e '.[dev]'
uv run pdf-batch ~/papers --mailto you@uni.nl --unpaywall
# (or, from the checkout: uv run pdf_batch.py ~/papers ...)
# → ~/papers/_metadata/papers.md, papers.ris, papers.json
```

`CONTACT_MAILTO` in a `.env` file (searched from the current directory upwards; see `.env.example`) works instead of `--mailto`. Reruns are incremental: PDFs are keyed by SHA-256, so only new files are processed and renamed files are recognised. Use `--force` to redo everything.

## How each PDF is identified (cheapest first)

| Tier | Source | Tokens |
|---|---|---|
| 1 | XMP (`prism:doi`, `dc:identifier`) and the PDF Info dict | 0 |
| 2 | Regex for DOI / arXiv ID: page-1 header, footer and margins, then page 1, page 2, last page. Reference lists are excluded. | 0 |
| 3 | Largest-font title on page 1 → CrossRef `query.bibliographic` → OpenAlex search | 0 |
| 4 | GROBID header parse (`--grobid http://localhost:8070`) | 0 |
| 5 | LLM search key from page 1 (`--llm anthropic` or `--llm ollama`), max ~1.5k tokens in | rare |

Every hit is **validated**: the API title is fuzzy-matched against the PDF text, and the authors' surnames are checked for. This stops a cited paper's DOI from being accepted. The result is a confidence of `high`, `medium`, `low` or `unresolved`. Anything below `medium` lands in the Markdown "Needs review" section and gets the tag `pdfbatch: needs review` in RIS.

The LLM never writes metadata. It only produces a title or DOI, which goes back through tier 3 and validation.

## Metadata sources

- **Bibliographic fields** come from CrossRef, or from arXiv for arXiv IDs: structured authors, journal, volume, issue, pages or article number, ISSN, item type, and preprint↔published relations.
- **OpenAlex** fills gaps and supplies OA data.
- **Europe PMC** supplies the PMID, PMCID and abstract.
- **Abstract priority:** CrossRef → Europe PMC → OpenAlex → PDF text.

## Open access

OA status is one of `gold`, `diamond`, `hybrid`, `bronze`, `green` or `closed`, with license, version and a free-copy URL where one exists.

- **Unpaywall** (`--unpaywall`) is used first, then OpenAlex.
- **arXiv, bioRxiv/medRxiv and other preprint DOIs** are green by rule.

A `closed` paper whose PDF you hold came through a subscription, so don't redistribute it.

## Code & data

Code and data links are found in the PDF full text plus its link annotations, which survive line-wrapped URLs. Patterns and a blocklist of commonly cited frameworks live in `pdfbatch/link_patterns.py`.

- **Links inside a References section are dropped**, because those are cited tools, not the paper's own code.
- **Link checking:** `--check-links` HEAD-checks code links (results cached for 30 days).

## Preprints

By default the record describes the version you hold, with the other version's DOI noted. `--prefer published` cites the published version instead when CrossRef or arXiv links one.

## RIS details

- **Fields:**
  - `TY`, then one `AU` (`Family, Given`) per author
  - `TI`, `T2`, `J2`, `PY`, `DA` (`YYYY/MM/DD/`)
  - `VL`, `IS`, `SP`/`EP` (or the article number in `SP` and `M1`), `SN`, `PB`
  - `DO`, `UR` (DOI link, plus the OA copy), `AN` (PMID), `AB`
- **`KW` keywords** for OA status, license, *Has code* / *Has data* and review flags. These become **tags in Zotero** and keywords in EndNote, so you can filter by them.
- **`N1`** holds the OA details, code/data links, notes and the resolution route.
- **`L1`** is a `file://` link that attaches the PDF on import:
  - Zotero: choose "Copy files to the Zotero storage folder" in the import dialog.
  - EndNote: the file is attached as a file link. Use `--attach path` if your EndNote prefers plain paths, or `--attach none`.
- **Encoding:** UTF-8 with CRLF line endings. In EndNote: *File → Import*, filter **Reference Manager (RIS)**, text translation **Unicode (UTF-8)**. Add `--bom` if an older EndNote garbles accents.
- **Preprints:** exported as `JOUR` with the server (bioRxiv, arXiv) as journal, which imports cleanly in both programs. Use `--preprint-ris-type UNPB` if you prefer "Unpublished work".

## Options worth knowing

| Option | Purpose |
|---|---|
| `--openalex-key` (env `OPENALEX_API_KEY`) | Use your own OpenAlex API key |
| `--workers 4` | Number of PDFs processed in parallel. APIs are throttled per host and cached for 30 days in `.pdfbatch_cache/` |
| `--recursive` | Include subfolders |

## Limits

- **Scanned PDFs** with no text layer need OCR first (`ocrmypdf in.pdf out.pdf`). They are flagged.
- **Theses, protocols and internal reports** usually have no DOI. They come out `unresolved` with PDF-derived fields only, so review them by hand.
- **PyMuPDF is AGPL.** If that matters for redistribution, swap `pdf_reader.py` to pypdf plus pdfplumber; the rest is independent of it.

## Tests

```bash
uv run pytest
```

The tests generate synthetic PDFs and mock the APIs with real response shapes, so they run offline. The cases covered are:

- a DOI in the header next to a decoy DOI cited on page 1
- an arXiv margin stamp
- a PDF with no DOI (title search)
- a bioRxiv DOI with a version suffix
- an unresolvable PDF
- references-section link exclusion
- RIS/Markdown structure
