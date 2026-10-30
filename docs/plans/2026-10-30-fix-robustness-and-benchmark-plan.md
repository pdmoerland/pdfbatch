---
title: Robustness fixes and an accuracy benchmark
type: fix
status: completed
date: 2026-10-30
iteration: 9
---

# pdfbatch iteration 9: fixes and benchmark — instructions for Claude Code

Oct 30, 2026 · @Perry Moerland

Seven tasks for one iteration: four bug fixes, three small fixes, a benchmark harness, and the matching docs. Hand the whole doc to Claude Code as its brief.

## How to use this and ground rules

Export this doc as Markdown and save it as `docs/plans/2026-10-30-fix-robustness-and-benchmark-plan.md`, then tell Claude Code: "Implement the plan in docs/plans/2026-10-30-fix-robustness-and-benchmark-plan.md, one task per commit." Claude Code also reads the repo's `CLAUDE.md`.

Rules for Claude Code:

- Work task by task, in order. One commit per task, with a Conventional Commits message in the repo's existing style (`fix(apis): …`, `feat(cli): …`, `test: …`).
- Every bug fix starts with a failing test in `tests/pdfbatch/test_pipeline.py` that reproduces it, then the fix. Tests stay offline: reuse the `http` fixture and `fixtures.make_pdfs`.
- Run `uv run pytest` after each task; all tests must pass before committing.
- Keep the existing style: specificity over sensitivity, small functions, docstrings that explain *why*. Do not reformat unrelated code.
- No new runtime dependencies. The benchmark (Task 6) may use only the standard library plus what is already installed.
- Do not change matching thresholds in this iteration. Task 6 exists so threshold changes can be measured later.

## Task 1: make the per-host throttle thread-safe

**Where:** `pdfbatch/apis.py`, `Http._throttle` and `Http.__init__`.

**Problem:** up to `--workers` threads read and write `self._last` without a lock. Two threads can both compute `wait <= 0` and hit the same host at once, so `min_interval` is not enforced.

**Fix:**

1. Add `self._lock = threading.Lock()` in `__init__`.
2. In `_throttle`, under the lock: compute the next free slot as `max(now, self._last.get(host, 0) + min_interval)`, store it in `self._last[host]`, and release the lock. Then sleep until that slot *outside* the lock, so other hosts are not blocked.
3. Reserving the slot before sleeping is the point: each thread claims a distinct time.

**Test:** `test_throttle_spaces_concurrent_requests`. Patch `Http.session.get` to record `time.monotonic()` and return a 200 JSON response. Run 8 calls to one host from a 4-thread pool with `min_interval=0.05` and no cache. Assert every gap between sorted timestamps is ≥ 0.045 s.

Switch `time.time()` to `time.monotonic()` in the throttle while you are there.

## Task 2: keep duplicate PDFs as separate records on reruns

**Where:** `pdfbatch/cli.py`, the loop over `pdfs` that reuses `known` records.

**Problem:** `known` maps SHA-256 to one `Record`. Two files with identical content get the same object; `rec.pdf_path` is overwritten, so both entries point at the second file and a `notes.md` entry for the first is reported as unmatched.

**Fix:** reuse a copy: `rec = copy.deepcopy(known[h])`, then set `pdf_path`. Keep both files as entries: they are distinct files the user holds. Add a note on the second and later copies: `"Same content as <first file name>."` so the user can spot duplicates.

**Test:** `test_duplicate_pdfs_keep_their_paths`. Copy one fixture PDF to two names, run `cli.main` twice (second run incremental), load the JSON and assert two records with the two distinct `pdf_path` values, and the duplicate note on one.

## Task 3: fail early and safely in the LLM tier

**Where:** `pdfbatch/llm.py` (`LlmIdentifier`), `pdfbatch/cli.py` (`main`), `pdfbatch/pipeline.py` (`_fallbacks`).

**Problem:** with `--llm anthropic` and no `ANTHROPIC_API_KEY`, `_anthropic` raises `RuntimeError` per PDF. `identify()` only catches `requests.RequestException`, so the error reaches the catch-all in `cli.process`, and the record is replaced by a "Crashed" stub. PDF-derived fields are lost. A non-JSON reply raises `ValueError` the same way.

**Fix:**

1. Check the key in `LlmIdentifier.__init__` for the anthropic backend; raise a clear `ValueError`. In `cli.main`, catch it and exit with code 2 and a one-line message, before any PDF is processed.
2. In `LlmIdentifier.identify`, catch `(requests.RequestException, ValueError, KeyError)`; return `None` and count the failure in a new `self.failures` counter.
3. In `cli.main`, report failures next to the token usage line: `LLM fallback failed for N PDF(s)`.

**Tests:** `test_llm_without_key_exits_early` (monkeypatch the env var away, `cli.main([... , "--llm", "anthropic"])` returns 2 and no output files exist). `test_llm_bad_reply_keeps_record`: monkeypatch `requests.post` to return a non-JSON body for an unresolvable fixture; assert the record is `unresolved` with its PDF-derived title, not "Crashed".

## Task 4: add `--retry-unresolved`

**Where:** `pdfbatch/cli.py`, `_arguments` and the reuse loop; `README.md` options table.

**Problem:** unresolved records are cached like resolved ones. After adding `--grobid`, `--llm` or Tesseract, the only way to retry them is `--force`, which redoes every PDF.

**Fix:** a `--retry-unresolved` flag. In the reuse loop, a known record with `confidence == "unresolved"` goes to `todo` when the flag is set. Records whose notes start with `"Crashed"` or `"Could not open PDF"` already have no hash and are retried; leave that as is.

**Test:** `test_retry_unresolved`. First run on the unresolvable fixture; then patch `fixtures.route` so the paper resolves; rerun without the flag (still unresolved) and with it (now resolved).

Also update the README: in the Limits section, replace "rerun with `--force` after installing it" with `--retry-unresolved`.

## Task 5: smaller fixes (one commit)

Four low-risk changes; none should alter any output.

1. **Hash each PDF once.** `cli.main` hashes every PDF with `sha256_file`, and `read_pdf` hashes it again. Pass the hash in: `read_pdf(path, sha256=None)` uses the given value when present; `process_pdf` takes an optional `sha256` and forwards it; `cli.process` passes the hash it already computed.
2. **One version string.** Set the User-Agent from the package version: `importlib.metadata.version("pdfbatch")`, falling back to `"0"` when not installed. Use it in `apis.Http` and `links.LinkChecker`.
3. **Thread-safe LLM counters.** Guard `calls`, `tokens_in`, `tokens_out` and the new `failures` in `LlmIdentifier` with a `threading.Lock`.
4. **Lint nits.** Fix Ruff's E741 (rename single-letter `l` to `link` or `line`), E702 (split the two `time.sleep(delay); delay *= 2` lines) and E501. Leave E731 (assigned lambdas) alone: they are deliberate one-liners. Add to `pyproject.toml`:

```toml
[tool.ruff]
line-length = 130

[tool.ruff.lint]
select = ["E", "F", "W", "B"]
ignore = ["E731"]
```

Add `ruff` to the `dev` extra and confirm `uv run ruff check pdfbatch tests` passes.

Do not do a general style rewrite. Only where you touch a dense one-liner anyway (for example in `validate()`), you may split it into named steps if that makes it clearer.

## Task 6: accuracy benchmark harness

A script that scores pdfbatch against a hand-checked list of the right DOI per PDF, so threshold changes can be measured. It runs against the real APIs and is not part of `pytest`.

**Files:**

- `bench/run_benchmark.py`: the script.
- `bench/gold.example.csv`: a three-row example of the format.
- `bench/README.md`: how to build a gold set and run it.
- Add `bench/gold.csv`, `bench/pdfs/` and `bench/results/` to `.gitignore`. PDFs are copyrighted and the gold set is personal; neither is committed.

**Gold format** (`gold.csv`, UTF-8):

| Column | Meaning |
| --- | --- |
| `sha256` | The PDF's hash; the key, so renamed files still match |
| `file` | File name, for humans only |
| `doi` | The correct DOI, lower case; empty when the item has none (thesis, report) |
| `status` | `verified` or `unverified`; only `verified` rows are scored |
| `comment` | Free text, such as "DOI only on last page" |

**Commands:**

1. `uv run python bench/run_benchmark.py init --pdfs bench/pdfs --gold bench/gold.csv` writes a gold file from a normal pdfbatch run: every row `unverified`, `doi` prefilled with what pdfbatch found. Never overwrite an existing gold file; add only new hashes.
2. `uv run python bench/run_benchmark.py run --pdfs bench/pdfs --gold bench/gold.csv [--mailto …] [--grobid URL] [--llm …]` runs `process_pdf` on every verified PDF (same `Settings` and HTTP cache as the CLI) and writes `bench/results/<timestamp>-<git short hash>.json`.
3. `… run … --compare bench/results/<earlier>.json` also lists every PDF whose outcome changed between the two runs.

**Outcome per PDF:**

| Outcome | Gold `doi` | pdfbatch result |
| --- | --- | --- |
| correct | set | resolved, same DOI |
| wrong | set or empty | resolved, different DOI (or a DOI where gold has none) |
| missed | set | unresolved |
| correctly unresolved | empty | unresolved |

Compare DOIs with `identifiers.clean_doi` on both sides. When pdfbatch cites the published version (`--prefer published`), also accept a match on `related_doi`.

**Printed summary:** counts and percentages per outcome; precision = correct ÷ (correct + wrong); the unresolved rate; a breakdown of correct and wrong by route (`resolved_via[0]`, such as `doi:page1-header` or `title-search`); and the file names of every wrong result, since those matter most. Exit code 1 when `--max-wrong N` is given and wrong exceeds N.

**Test:** put the scoring in a pure function, `score(gold_rows, records) -> dict`, and add `test_benchmark_scoring` with synthetic rows covering all four outcomes and the `related_doi` case. Make `bench` importable for that test (an empty `bench/__init__.py`).

## Task 7: docs, plan file and done criteria

Finish by recording the iteration the way the repo already does.

1. Give this plan file the repo's frontmatter: `title: Robustness fixes and an accuracy benchmark`, `type: fix`, `status: completed`, `date: 2026-10-30`, `iteration: 9`. Add a short "What changed" section at the end listing the commits.
2. Add iteration 9 to the Implementation history table in `design_decisions_and_rationale.md`, with its commit, matching the existing rows.
3. In the same file, add two short paragraphs: under Incremental reruns, why duplicates stay separate and what `--retry-unresolved` is for; under Validation, that thresholds are now measured with `bench/` and should change only with a before-and-after benchmark run.
4. README: add `--retry-unresolved` to the options table and a one-paragraph "Measuring accuracy" section that links `bench/README.md`.
5. `CLAUDE.md`: add `bench/` to the Layout table and the Ruff command to Running.

**Done when:**

- [ ] Every task has its own commit, and each bug fix has a test that failed before the fix
- [ ] `uv run pytest` passes offline
- [ ] `uv run ruff check pdfbatch tests` passes
- [ ] `uv run pdf-batch --help` shows `--retry-unresolved`
- [ ] `bench/run_benchmark.py init` and `run` work on a folder of 3 real PDFs (the user runs this, since it needs network access and real PDFs)
- [ ] Plan file, design doc, README and `CLAUDE.md` are updated

## What changed

One commit per task, each bug fix preceded by a test that failed before it:

| Task | Commit | Summary |
| --- | --- | --- |
| 1 | `f240d2a` fix(apis): make the per-host throttle thread-safe | Slots reserved under a lock, slept for outside it; `time.monotonic()`. `test_throttle_spaces_concurrent_requests` |
| 2 | `9bc4006` fix(cli): keep duplicate PDFs as separate records on reruns | A deep copy per file; later copies noted "Same content as <first>.". `test_duplicate_pdfs_keep_their_paths` |
| 3 | `264257b` fix(llm): fail early without an API key, and safely on a bad reply | Key checked before any PDF (exit 2); bad replies counted, not crashed. `test_llm_without_key_exits_early`, `test_llm_bad_reply_keeps_record` |
| 4 | `7e32614` feat(cli): add --retry-unresolved | Also in the scan note and README Limits. `test_retry_unresolved` |
| 5 | `accd612` refactor: hash each PDF once, one version string, Ruff clean | Ruff config and dev dependency; E741/E702/E501 fixed, plus B905 in a new test |
| 6 | `33fb1b3` feat(bench): accuracy benchmark against a hand-checked gold set | `bench/run_benchmark.py` (`init`, `run`, `--compare`, `--max-wrong`), `score()` tested by `test_benchmark_scoring` |
| 7 | docs: iteration 9 | This file, design doc, README, `CLAUDE.md` |

Notes:

- `pipeline._fallbacks` needed no change for Task 3: `LlmIdentifier.identify` now catches the failures itself, and `_parse` only returns keys that have a title.
- A resolved record without a DOI (a PubMed-only paper) counts as correct when the gold DOI is empty, and as wrong when it is set.
- Two benchmark runs in the same second get distinct result files (`-2`, `-3`, …).
- 57 tests pass offline; `uv run ruff check pdfbatch tests bench` passes. Running `bench/run_benchmark.py init` and `run` on real PDFs is left to the user (network access, real PDFs).
