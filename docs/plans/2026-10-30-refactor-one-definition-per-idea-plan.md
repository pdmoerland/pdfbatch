---
title: One definition per idea
type: refactor
status: completed
date: 2026-10-30
iteration: 14
---

# pdfbatch iteration 14: one definition per idea

Oct 30, 2026 · @Perry Moerland

A review of the whole package in the spirit of Dijkstra's "Beauty is our business": remove redundancy, keep one definition per idea, and keep behaviour intact. No unused definitions were found; the redundancy is ideas written down more than once.

## Changes, one commit each

1. **The lookup options, once.** `cli.py` and `bench/run_benchmark.py` each declare `--mailto`, `--openalex-key`, `--grobid`, `--llm`, `--llm-model`, `--ollama-url`, `--workers` and `--cache-dir`, resolve the default cache folder, require `--mailto`, build the LLM tier and build `Http` + `Settings`. The benchmark must run pdfbatch exactly as the CLI does, so these become one definition in `cli.py` that the benchmark imports: `add_lookup_options`, `check_lookup_options`, `make_llm`, `make_lookup`. The benchmark still uses no Unpaywall and no link checks.
2. **"Unresolved", once.** `Record.needs_review` already defines it; `cli.py` (`--retry-unresolved`) and the benchmark compare `confidence == "unresolved"` again.
3. **The 30-day cache lifetime, once.** `Http` and `LinkChecker` each compute `ttl_days * 86400` from their own default of 30, and nothing passes another value. One constant, `apis.CACHE_TTL`.
4. **"Preprint servers are green OA", once.** `apis.arxiv_work` and `pipeline.enrich` each build the same `OpenAccess(is_oa=True, status="green", version="submittedVersion", source="rule", …)`. It becomes `OpenAccess.preprint_server(url)` in `record.py`.
5. **API-record helpers, once each** (`apis.py`): a year is the first four characters of a date (written three times), and a DOI goes into a URL path quoted with `safe="/"` (three times).
6. **Link labels and URLs, once each** (`links.py`): the tag alternation is built twice from `_TAGS` (sorted for `_TAG_RE`, unsorted for `_TAG_BEFORE`; alternation order does not change whether a `$`-anchored search matches), and a URL is trimmed of fragment, query and trailing slash twice.
7. **Page 1 before the references, once** (`identifiers.py`): written twice since iteration 12.

## Deliberately left

- `f"https://doi.org/{doi}"` (about ten places) and `Path(r.pdf_path).name`: a format string and an idiom, not ideas with rules of their own; a helper would add indirection, not clarity.
- `" ".join(s.split())` and `re.sub(r"\s+", " ", s)`: the same idiom, in modules that share no text utility. A text module for one line is not worth it.
- The two LLM backends' request code: different APIs, different shapes.
- The User-Agents of `Http` and `LinkChecker`: deliberately different.

## How behaviour is kept intact

- `uv run pytest` and Ruff after every commit.
- Both reading lists (92 PDFs) rerun with `--force` (through the cache) before and after, and the full `papers.json` and `papers.ris` compared; `papers.md` compared without its timestamp line.
- `pdf-batch --help` and `bench/run_benchmark.py run --help` checked by eye: same options, same defaults.

## Result

Seven commits, `c79f7b6` … `7ae9ca7`. 69 tests and Ruff pass after each. The comparison on the reading lists (92 PDFs; `papers.json` as a set of records, since its order follows completion; `papers.ris` byte for byte; `papers.md` without its timestamp line) was identical to the baseline after every commit that touched lookups, API parsing, links or identifiers. `pdf-batch --help` lists the same options; the lookup options now come before `--unpaywall`, and the benchmark's `--help` gains the CLI's help texts.

## Done when

- Seven commits, tests and Ruff green, the comparison on the reading lists identical.
- History table row 14; `status: completed`.
