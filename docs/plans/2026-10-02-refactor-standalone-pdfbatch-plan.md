---
title: Make pdfbatch a standalone tool
type: refactor
status: completed
date: 2026-10-02
iteration: 2
---

# Iteration 2 — Standalone tool

## Overview

This iteration pruned the paperbot repository down to pdfbatch alone. Afterwards the tool installs and runs without any bot code, and the repository has its own single-commit history.

## Decoupling

pdfbatch imported five things from the bot's `src/`. Each was copied into the package and the imports were repointed:

| New file | Copied from |
|---|---|
| `pdfbatch/link_patterns.py` | `src/enrichers/patterns.py` + the blocklist from `src/extractors/__init__.py` (as `BLOCKLIST_URLS`) |
| `pdfbatch/link_labeler.py` | `src/enrichers/heuristic_labeler.py` |
| `pdfbatch/link_validator.py` | `src/enrichers/validator.py` (bot logger → stdlib `logging`; own User-Agent) |
| `pdfbatch/citation.py` | `format_authors_apa`, `format_single_author`, `to_sentence_case` |

Docstrings and the API User-Agent no longer mention the paperbot.

## Pruning

- **Removed:** the bot's code and everything around it: `src/`, `run.py`, `set_cron_job.sh`, `scripts/`, `config/`, `data/`, `logs/`, `notebooks/`, `docs/`, the bot's unit and integration tests, example HTML pages, the old design-decisions doc and `.claude/`. That is about 200 files.
- **Kept:** `config/.env` holds secrets and is gitignored. It was **moved** to the repo root rather than deleted. The Slack keys in it are now unused.
- **Deleted:** a stray `pdfbatch.patch`.

## Packaging

- **`pyproject.toml`:**
  - **Project:** renamed `pdfbatch`.
  - **Runtime dependencies:** `requests`, `diskcache`, `python-dotenv`, `pymupdf`, `rapidfuzz`.
  - **Dev extra:** `pytest`.
  - **Packages:** only `pdfbatch`.
- **Lock file:** `uv lock` dropped the Slack, crawl4ai, vcrpy and other bot dependencies.
- **CLI:** moved to `pdfbatch/cli.py` and exposed as the console script **`pdf-batch`**. `pdf_batch.py` stayed as a thin wrapper for running from a checkout.
- **Secrets:** `.env` is now found by `find_dotenv(usecwd=True)`, i.e. the nearest `.env` from the working directory upward. A `.env.example` was added.
- **`.gitignore`:** trimmed to Python, venv, secrets, `.pdfbatch_cache/` and test artefacts.
- **Docs:** `pdfbatch/README.md` became the top-level `README.md`. `CLAUDE.md` was rewritten for pdfbatch.
- **Fix:** the `→` in the CLI description was replaced with `->`, so `--help` no longer crashes on a Windows cp1252 console.

## Git history

1. **First commit:** `refactor: make pdfbatch a standalone tool`, on the `pdfbatch` branch on top of the bot history.
2. **Stripping the bot history:** an orphan branch with the same tree was committed as a single commit (`ec51861`), then renamed to `main`. pdfbatch's files had only entered git in that last commit, so nothing about the tool was lost.
3. **Clean-up:** the bot's `main` (temporarily `bot-main`) and the `pdfbatch` branch were deleted, and the `origin` remote, which pointed to the upstream bot repository, was removed. The bot itself is untouched upstream.

## Verification

- `uv run pytest`: 15 passed in a freshly synced venv.
- `uv run pdf-batch --help` and `uv run pdf_batch.py --help` both work.
