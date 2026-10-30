---
title: Continuous integration on Linux, macOS and Windows
type: feat
status: in-progress
date: 2026-10-30
iteration: 18
---

# pdfbatch iteration 18: CI on Linux, macOS and Windows

Oct 30, 2026 · @Perry Moerland

From an external review: there is no CI, and several code paths are platform-specific (Tesseract folders, the cache folder, the hint about a stray `\"` in a Windows path). A GitHub Actions matrix should run pytest, Ruff and mypy on all three systems.

## 1. mypy passes, and is a dev dependency

`uv run --with mypy mypy pdfbatch bench tests` reports 21 errors today. All are fixed without changing behaviour:

- **Missing annotations** for empty containers (`Counter()`, `[]`, `{}`).
- **Stubs:** `types-requests` as a dev dependency; `diskcache` ships no types, so it alone is ignored by an override.
- **Optional values used as if set**, each where the code path guarantees them: `_grobid` gets the URL as an argument rather than `Settings`; `_retry_after` and `_one_line` take `str | None` explicitly; a good hit always carries its record.
- **Verdict vs. Confidence** (iteration 7's two vocabularies): `_apply` only ever receives a good verdict, which is a valid `Confidence`; typed as such.
- **PyMuPDF:** `for page in doc` becomes `doc.pages()`, the same iteration in a form its type hints know.

`[tool.mypy]` in `pyproject.toml` names the files to check, so `uv run mypy` needs no arguments.

## 2. Tests for the platform branches, on every platform

`default_cache_dir` has Windows, macOS and Linux branches, but only the environment variable and Linux are tested. The quoting hint in `cli.main` is not tested. New tests fake `sys.platform` (and `LOCALAPPDATA` / the home folder) so every branch runs on every runner; the real platform then also runs its own branch without faking.

## 3. The workflow

`.github/workflows/ci.yml`, on push and pull request to `main`:

- **Matrix:** `ubuntu-latest`, `macos-latest`, `windows-latest` × Python 3.11 (the minimum in `requires-python`) and 3.13. `fail-fast: false`, so one system's failure does not hide the others'.
- **Steps:** checkout; `astral-sh/setup-uv` with caching; `uv sync --locked --extra dev` (fails if `uv.lock` is stale); `uv run ruff check pdfbatch tests bench`; `uv run mypy`; `uv run pytest`.
- **Offline:** the tests mock every API, so CI needs no secrets and no network beyond installing packages. The benchmark (real APIs) stays out of CI.
- **Tesseract is not installed:** the OCR tests already fake it.

## Found on the way

Running the suite on Python 3.11 locally (Windows) failed the throttle test about one run in three: before Python 3.13, `time.monotonic` on Windows ticks every 15.6 ms, so throttle slots could come up to 15 ms early. The throttle now uses `time.perf_counter`, high-resolution everywhere (`b20b234`); 0 failures in 15 runs.

## Result

`81c672f` (mypy), `2884c6a` (platform tests), `b20b234` (throttle clock), `b721dc6` (workflow). Locally on Windows: 79 tests, Ruff and mypy pass on Python 3.11 and 3.13; output for 92 real PDFs is byte-identical.

## Done when

- Ruff, mypy and pytest pass locally on Windows.
- Pushed, and the workflow is green on all six jobs (checked through GitHub's API).
- README ("Running tests"), `CLAUDE.md` and the design doc's history table updated; `status: completed`.
