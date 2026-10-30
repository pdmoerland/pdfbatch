---
title: Fixes from a code review
type: fix
status: completed
date: 2026-10-30
iteration: 11
---

# pdfbatch iteration 11: code review fixes — instructions for Claude Code

Oct 30, 2026 · @Perry Moerland

Nine tasks from an external code review: three robustness fixes, two security/usability fixes, three small fixes, an optional typing cleanup, and the matching docs. None changes what pdfbatch identifies or how; matching thresholds stay exactly as they are. Hand the whole doc to Claude Code as its brief.

## How to use this and ground rules

Save this file as `docs/plans/2026-10-30-fix-review-findings-plan.md`, then tell Claude Code: "Implement the plan in docs/plans/2026-10-30-fix-review-findings-plan.md, one task per commit." Claude Code also reads the repo's `CLAUDE.md`.

Rules for Claude Code:

- Work task by task, in order. One commit per task, with a Conventional Commits message in the repo's existing style (`fix(apis): …`, `fix(cli): …`, `test: …`, `docs: …`).
- Every fix starts with a failing test in `tests/pdfbatch/test_pipeline.py` that reproduces it, then the fix. Tests stay offline: reuse the `http` fixture, `fixtures.make_pdfs` and `fixtures.route`.
- Run `uv run pytest` and `uv run ruff check pdfbatch tests bench` after each task; both must pass before committing.
- Keep the existing style: specificity over sensitivity, small functions, docstrings that explain *why*. Do not reformat unrelated code.
- No new runtime dependencies. In particular, do not add `platformdirs` for Task 4; the standard library is enough.
- Do not change matching thresholds, tier order or validation rules. If a task seems to need that, stop and ask.
- Run the benchmark (`bench/run_benchmark.py`) before Task 1 and after Task 8 if `bench/gold.csv` exists locally. Scores must be identical; report both in the final summary. Skip this silently if there is no gold set.

## Task 1: write output files atomically

**Where:** `pdfbatch/writers.py` (`write_json`, `write_markdown`, `write_ris`).

**Problem:** all three writers call `path.write_text(...)`, which truncates the file and then writes. A crash, Ctrl-C or full disk midway leaves a truncated `papers.json`. Combined with Task 2's problem, the next run silently forgets every cached record.

**Fix:**

1. Add a private helper `_write_atomic(path: Path, text: str, *, newline: str | None = None) -> None`. It writes to a temporary file in the *same directory* (`tempfile.NamedTemporaryFile(dir=path.parent, delete=False, ...)`, so the rename stays on one filesystem), flushes and `os.fsync`s, closes it, then `os.replace(tmp, path)`. On any exception it removes the temp file and re-raises.
2. Use it in all three writers. `write_ris` must keep `newline=""` (RIS needs its explicit `\r\n`) and the optional BOM; `write_json` and `write_markdown` keep the default newline handling they have now.
3. Docstring: one line on why (an interrupted run must not destroy the source of truth).

**Test:** `test_json_write_is_atomic`. Write a valid `papers.json` with `write_json`. Then monkeypatch `json.dumps` (or the helper's write) to raise halfway, call `write_json` with different records, and assert the original file is unchanged and no temp files are left in the directory.

## Task 2: don't silently discard an unreadable `papers.json`

**Where:** `pdfbatch/writers.py` (`read_json`), `pdfbatch/cli.py` (`main`).

**Problem:** `read_json` returns `[]` on `ValueError`/`TypeError`. The run then reprocesses every PDF and overwrites the broken file, and the user never learns why a rerun took an hour.

**Fix:**

1. `read_json` keeps returning `[]` for a missing file, but lets parse errors propagate (raise `ValueError` with the path in the message for both `ValueError` and `TypeError` from `Record.from_dict`).
2. In `cli.main`, when not `--force`: catch that error, rename the broken file to `<name>.json.bak` (replace an older `.bak`), log one line: `papers.json could not be read (<error>); kept it as papers.json.bak and reprocessing every PDF.`, and continue with `known = {}`.

**Test:** `test_corrupt_json_is_kept_and_reported`. Write `"[{"` to `<out>/papers.json`, run `cli.main` on the fixture folder, assert exit code 0, that `papers.json.bak` contains `"[{"`, that a new valid `papers.json` exists, and that the log line is in `capsys.readouterr().err`.

## Task 3: cap `Retry-After`

**Where:** `pdfbatch/apis.py`, `Http.get`.

**Problem:** `time.sleep(float(ra))` trusts the server. `Retry-After: 3600` stalls a worker for an hour, per retry.

**Fix:** add a module constant `MAX_RETRY_AFTER = 60.0` (seconds) and sleep `min(float(ra), MAX_RETRY_AFTER)`. Also accept decimal values (`"1.5"`): replace the `ra.isdigit()` check with a `try: float(ra)` that falls back to `delay` on `ValueError`. HTTP-date values keep falling back to `delay`.

**Test:** `test_retry_after_is_capped`. Build an `Http` with `cache_dir=None`; monkeypatch its `session.get` to return a 429 with `Retry-After: 3600` once, then a 200 JSON response; monkeypatch `apis.time.sleep` to record its arguments. Assert the result is the JSON body and every recorded sleep is ≤ 60.

## Task 4: keep secrets out of the cache, and give the cache a fixed home

**Where:** `pdfbatch/apis.py` (`Http.get`, new `default_cache_dir`), `pdfbatch/cli.py` and `bench/run_benchmark.py` (`--cache-dir` default), `.gitignore`.

**Problem (a):** the cache key is the URL plus *all* params, so the OpenAlex `api_key` is stored in plain text in the diskcache SQLite file.

**Problem (b):** `--cache-dir` defaults to `.pdfbatch_cache` relative to the current directory, so runs from different folders don't share a cache, and a cache can land inside a synced or shared folder.

**Fix:**

1. In `Http.get`, build the cache key from the params *without* `api_key` (a module constant `_UNCACHED_PARAMS = {"api_key"}`). The request itself still sends it.
2. Add `default_cache_dir() -> Path` in `apis.py`, standard library only:
   - `PDFBATCH_CACHE_DIR` if set;
   - Windows: `%LOCALAPPDATA%\pdfbatch\Cache`;
   - macOS: `~/Library/Caches/pdfbatch`;
   - otherwise `$XDG_CACHE_HOME/pdfbatch`, falling back to `~/.cache/pdfbatch`.
3. `--cache-dir` defaults to `None` in both `cli.py` and `bench/run_benchmark.py`; resolve `None` to `default_cache_dir()` after parsing. Update its `help` text to name the default.
4. Keep `.pdfbatch_cache/` in `.gitignore` (old checkouts still have one).

**Tests:**

- `test_api_key_not_in_cache_key`: a real `Http` with `cache_dir=tmp_path`, `session.get` monkeypatched to return a 200 JSON response; call `get(url, params={"api_key": "SECRET", "q": "x"})`; assert `"SECRET"` appears in no key of `h.cache` (`list(h.cache.iterkeys())`), and that a second identical call is served from cache (`session.get` called once).
- `test_default_cache_dir`: with `PDFBATCH_CACHE_DIR` set via monkeypatch it wins; with it unset and `XDG_CACHE_HOME` set on a non-Windows, non-macOS `sys.platform` (monkeypatch `sys.platform` to `"linux"`), the result is `<XDG_CACHE_HOME>/pdfbatch`.

## Task 5: check only the authors `validate` uses

**Where:** `pdfbatch/pipeline.py`, `validate`.

**Problem:** without `all_authors`, only the first five checkable surnames matter (`any(found[:5])`), but the list comprehension runs `_name_in` for *every* author first. A physics paper with thousands of authors costs thousands of regex scans and fuzzy matches per candidate, for several candidates.

**Fix:** build the list of checkable surnames first (normalised, `len(s) > 1`, as now), slice it to five unless `all_authors`, then run `_name_in` on that slice. Use `any(...)`/`all(...)` over a generator so the check short-circuits. Behaviour must be identical: the same names are considered, in the same order, with the same one-letter filter applied *before* the slice. An empty list still means "not named".

**Test:** `test_validate_checks_at_most_five_authors`. Monkeypatch `pipeline._name_in` with a counting wrapper around the real function; validate a record with 1,000 authors against a fixture PDF. Assert at most 5 calls without `all_authors`. Existing validation tests must pass unchanged.

## Task 6: escape Markdown in the APA line

**Where:** `pdfbatch/writers.py`, `apa` (and `_apa_name` if simpler).

**Problem:** `apa` wraps the journal and volume in `*…*` but inserts title, journal and author names raw. A title such as `The *lac* operon` or a journal with `_` breaks the italics in `papers.md`.

**Fix:** a small `_md_escape(s)` that backslash-escapes `\ * _ [ ] \``. Apply it to the title, the journal name and each author name inside `apa`, not to the DOI/URL. `apa` is only used for Markdown (check with `grep -rn "apa(" pdfbatch bench`; if it is used anywhere else, escape in `_md_entry` instead and say so in the commit message).

**Test:** `test_apa_escapes_markdown`. A record titled `The *lac* operon_1`, journal `J_Mol [Biol]`, author family `O*Brien`; assert the escaped forms appear and that the journal is still wrapped in exactly one pair of unescaped `*`. Existing `apa` tests must pass unchanged.

## Task 7: name the two confidence vocabularies

**Where:** `pdfbatch/pipeline.py`, `pdfbatch/record.py`.

**Problem:** `validate` returns `"low"`, while `Record.confidence` uses `"unresolved"`. Both are deliberate (a verdict on one candidate vs. the outcome for the PDF), but nothing in the code says so, and it reads like an inconsistency.

**Fix:** no behaviour change, no renamed values (tests and saved JSON files depend on them).

1. In `pipeline.py`: `Verdict = Literal["high", "medium", "low"]`, used in `validate`, `_title_search`, `_lookup`, `Hit` and `_RANK`. A comment: a verdict is about one API record; `low` means "this record is not this PDF".
2. In `record.py`: `Confidence = Literal["high", "medium", "unresolved"]` for `Record.confidence`, with a comment: the outcome for the PDF; `unresolved` means no record validated.

**Test:** none new; `uv run pytest` and Ruff pass.

## Task 8: test the Ollama backend

**Where:** `tests/pdfbatch/test_pipeline.py` only. `pdfbatch/llm.py` is at 65 % coverage; the Ollama path is untested.

**Tests:**

- `test_ollama_reply_is_parsed`: `LlmIdentifier("ollama")`; monkeypatch `llm.requests.post` to return a response whose `.json()` is `{"message": {"content": "<think>x</think>```json\n{\"title\": \"A Paper\", \"first_author_surname\": \"Doe\", \"year\": null, \"doi\": null}\n```"}, "prompt_eval_count": 100, "eval_count": 20}` and whose `raise_for_status()` does nothing. Assert `identify("page text")["title"] == "A Paper"`, `tokens_in == 100`, `tokens_out == 20`, `calls == 1`, `failures == 0`. Also assert the posted JSON contains `"format"` (the schema) and `"think": False`.
- `test_ollama_unreachable_counts_a_failure`: `requests.post` raises `requests.ConnectionError`; `identify` returns `None` and `failures == 1`.

If these expose a bug, fix it in the same commit and say so in the message.

## Task 9 (optional): modern type syntax

**Where:** `pdfbatch/`, `tests/`, `bench/`, `pyproject.toml`.

`requires-python` is `>=3.11`, so `typing.List`/`Optional`/`Tuple`/`Dict` can become `list`, `X | None`, `tuple`, `dict`.

1. Add `"UP"` to `[tool.ruff.lint] select`.
2. Run `uv run ruff check --select UP --fix pdfbatch tests bench`, review the diff, and fix by hand anything Ruff can't.
3. Pure mechanics: no logic changes in this commit. Skip this task if it conflicts with anything above, and say so.

## Task 10: docs

**Where:** `README.md`, `design_decisions_and_rationale.md`, `bench/README.md`, this plan's frontmatter.

1. `README.md`, options table: `--cache-dir` now defaults to the per-user cache folder (list the three platform paths and `PDFBATCH_CACHE_DIR`). Add one sentence: an old `.pdfbatch_cache/` folder from earlier versions is no longer used and can be deleted; it may contain an OpenAlex API key.
2. `README.md`: mention `papers.json.bak` where reruns are described.
3. `design_decisions_and_rationale.md`, "Being a polite API client": update the cache location; add that `Retry-After` is honoured up to 60 s and that API keys are not part of cache keys. Under "Outputs", one sentence on atomic writes and the `.bak` file.
4. `bench/README.md`: the `--cache-dir` default.
5. Add row 11 to the "Implementation history" table, linking this plan, with the commit hashes (first … last).
6. Set `status: completed` in this file's frontmatter.

## Done when

- All tasks committed in order (Task 9 may be skipped with a note), `uv run pytest` and Ruff pass.
- Benchmark scores before and after are identical, or there is no local gold set.
- A short summary lists each commit, any task that turned up a further bug, and anything deliberately left out.
