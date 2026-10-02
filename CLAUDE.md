# pdfbatch

CLI that turns a folder of PDFs into `<name>.md` + `<name>.ris` (EndNote/Zotero) + `<name>.json`, DOI-first. See `README.md` for the full behaviour and options.

## Layout

| Where | What |
|---|---|
| `pdfbatch/cli.py` | Argument parsing, incremental (SHA-256) batching, thread pool, writes outputs. Entry point `pdf-batch`; `pdf_batch.py` is a checkout shim. |
| `pdfbatch/pipeline.py` | `process_pdf`: identification tiers → API merge → validation/confidence → code & data links. |
| `pdfbatch/pdf_reader.py` | PyMuPDF text, fonts, XMP, link annotations. |
| `pdfbatch/identifiers.py` | DOI / arXiv candidate extraction (reference lists excluded). |
| `pdfbatch/apis.py` | Cached, throttled clients: CrossRef, OpenAlex, Europe PMC, arXiv, Unpaywall, GROBID. |
| `pdfbatch/llm.py` | Optional last-resort LLM (Anthropic / Ollama) that only produces a search key. |
| `pdfbatch/link_patterns.py`, `link_labeler.py`, `link_validator.py` | Code/data URL patterns + blocklist, heuristic labels, HEAD-check with diskcache. |
| `pdfbatch/record.py`, `writers.py`, `citation.py` | Data model; Markdown / RIS / JSON writers; APA author formatting. |

## Running

```bash
uv venv && uv pip install -e '.[dev]'
uv run pdf-batch ~/papers --unpaywall
uv run pytest                     # offline: synthetic PDFs + mocked APIs
```

## Conventions

- Secrets and contact e-mail go in `.env` (gitignored), never in committed files.
- The LLM never writes metadata; it only proposes a title/DOI that goes back through API lookup + validation.
