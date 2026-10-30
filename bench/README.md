# Accuracy benchmark

Scores pdfbatch against a hand-checked list of the right DOI per PDF, so a change to the matching thresholds (or anything else in identification) can be measured before and after. It runs against the real APIs, through the same HTTP cache as `pdf-batch`, and is not part of `pytest`.

Nothing personal is committed: `bench/pdfs/`, `bench/gold.csv` and `bench/results/` are gitignored. PDFs are copyrighted and the gold set is yours.

## Build a gold set

1. Put PDFs in `bench/pdfs/` (subfolders are fine). A useful set mixes the easy cases with the ones that go wrong: scans, theses, preprints that have since been published, papers without a DOI, slides, reports.
2. Prefill the gold file from a normal pdfbatch run:

   ```bash
   uv run python bench/run_benchmark.py init --pdfs bench/pdfs --gold bench/gold.csv
   ```

   Every new PDF gets a row with `status` `unverified` and the DOI pdfbatch found. `init` never rewrites an existing gold file: it only appends rows for PDFs whose hash is not in it yet, so run it again after adding PDFs.
3. Check every row by hand: correct the `doi` (lower case; empty when the item has no DOI, such as a thesis or a report) and set `status` to `verified`. Only verified rows are scored.

`gold.example.csv` shows the format:

| Column | Meaning |
| --- | --- |
| `sha256` | The PDF's hash; the key, so renamed files still match |
| `file` | File name, for humans only |
| `doi` | The correct DOI; empty when the item has none |
| `status` | `verified` or `unverified` |
| `comment` | Free text, such as "DOI only on last page"; `init` prefills how pdfbatch found it |

## Run it

```bash
uv run python bench/run_benchmark.py run --pdfs bench/pdfs --gold bench/gold.csv
```

`--mailto`, `--openalex-key`, `--grobid`, `--llm`, `--llm-model`, `--ollama-url`, `--workers` and `--cache-dir` are the CLI's own options, defined once in `pdfbatch/cli.py`, and mean what they mean for `pdf-batch` (so the default cache is the same per-user folder: `PDFBATCH_CACHE_DIR`, else e.g. `%LOCALAPPDATA%\pdfbatch\Cache` or `~/.cache/pdfbatch`). Each run writes `bench/results/<timestamp>-<git short hash>.json` (`-dirty` when `pdfbatch/` has uncommitted changes) and prints:

- per outcome, its count and percentage:

  | Outcome | Gold `doi` | pdfbatch result |
  | --- | --- | --- |
  | correct | set | resolved, same DOI |
  | wrong | set or empty | resolved, different DOI (or a DOI where gold has none) |
  | missed | set | unresolved |
  | correctly unresolved | empty | unresolved |

- precision, correct ÷ (correct + wrong), and the unresolved rate;
- correct and wrong per route, the first step of `resolved_via` (`doi:page1-header`, `title-search`, …);
- the file name of every wrong result: those matter most, since a wrong record may never be noticed.

DOIs are compared after the same cleaning pdfbatch applies. A paper resolved without a DOI (found by PMID, say) is correct when the gold `doi` is empty, and wrong when it is set. For a preprint, the right DOI is the preprint's, even when a journal version exists: pdfbatch describes the PDF you hold.

Options for comparing:

- `--compare bench/results/<earlier>.json` lists every PDF whose outcome changed since that run.
- `--max-wrong N` exits with code 1 when more than N results are wrong.

A threshold change is worth keeping when the before-and-after runs show fewer wrong results, or more correct ones at no cost in wrong ones.
