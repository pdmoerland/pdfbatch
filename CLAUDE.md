# pdfbatch

CLI that turns a folder of PDFs into `<name>.md` + `<name>.ris` (EndNote/Zotero) + `<name>.json`, DOI-first. See `README.md` for the full behaviour and options.

## Layout

| Where | What |
|---|---|
| `pdfbatch/cli.py` | Argument parsing, incremental (SHA-256) batching, thread pool, writes outputs. Entry point `pdf-batch`. |
| `pdfbatch/pipeline.py` | `process_pdf`: identification tiers → API merge → validation/confidence → code & data links. |
| `pdfbatch/pdf_reader.py` | PyMuPDF text, fonts, XMP, link annotations. |
| `pdfbatch/identifiers.py` | DOI / arXiv candidate extraction (reference lists excluded). |
| `pdfbatch/apis.py` | Cached, throttled clients: CrossRef, OpenAlex, Europe PMC, arXiv, Unpaywall, GROBID. |
| `pdfbatch/llm.py` | Optional last-resort LLM (Anthropic / Ollama) that only produces a search key. |
| `pdfbatch/links.py` | Code/data platforms + blocklist, heuristic labels, cached HEAD-check, `find_links`. |
| `pdfbatch/record.py`, `writers.py` | Data model; APA, Markdown, RIS and JSON writers. |

## Running

```bash
uv sync --extra dev               # works in any shell; Windows PowerShell 5.1 has no &&
uv run pdf-batch ~/papers --unpaywall
uv run pytest                     # offline: synthetic PDFs + mocked APIs
```

## Conventions

- Secrets and contact e-mail go in `.env` (gitignored), never in committed files.
- The LLM never writes metadata; it only proposes a title/DOI that goes back through API lookup + validation.

## Design history

- `design_decisions_and_rationale.md` — the why behind tiers, validation, outputs and code shape.
- `docs/plans/YYYY-MM-DD-<type>-<slug>-plan.md` — one file per implementation iteration, with `status` and `iteration` in the frontmatter. Add one for each new iteration and link it from the design doc's "Implementation history" table.
