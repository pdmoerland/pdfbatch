"""Score pdfbatch against a hand-checked gold set: the right DOI per PDF.

    uv run python bench/run_benchmark.py init --pdfs bench/pdfs --gold bench/gold.csv
    uv run python bench/run_benchmark.py run  --pdfs bench/pdfs --gold bench/gold.csv [--compare OLD.json]

Runs against the real APIs (through the CLI's HTTP cache), so it is not part of
pytest. See bench/README.md for how to build a gold set.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from dotenv import find_dotenv, load_dotenv

from pdfbatch import apis
from pdfbatch.identifiers import clean_doi
from pdfbatch.llm import LlmIdentifier
from pdfbatch.pdf_reader import sha256_file
from pdfbatch.pipeline import Settings, process_pdf
from pdfbatch.record import Record

FIELDS = ["sha256", "file", "doi", "status", "comment"]
OUTCOMES = ["correct", "wrong", "missed", "correctly unresolved"]
RESULTS = Path(__file__).parent / "results"


# ---------------------------------------------------------------------------
# scoring (pure; tested offline)
# ---------------------------------------------------------------------------

def _doi(value: Optional[str]) -> Optional[str]:
    return clean_doi(value) if value and value.strip() else None


def outcome(gold_doi: Optional[str], rec: Record) -> str:
    """One PDF's outcome. A wrong DOI is the expensive mistake, so anything resolved
    that does not match the gold DOI counts as wrong, including a DOI where gold has none."""
    gold = _doi(gold_doi)
    if rec.confidence == "unresolved":
        return "missed" if gold else "correctly unresolved"
    return "correct" if _doi(rec.doi) == gold else "wrong"


def score(gold_rows: Iterable[dict], records: Iterable[Record]) -> dict:
    """Outcomes per verified gold row, matched to records by SHA-256, and their summary."""
    by_hash = {r.pdf_sha256: r for r in records}
    per_pdf, absent = [], []
    for row in gold_rows:
        if row.get("status", "").strip().lower() != "verified":
            continue
        rec = by_hash.get(row["sha256"].strip())
        if rec is None:
            absent.append(row["file"])
            continue
        per_pdf.append({"sha256": rec.pdf_sha256, "file": row["file"], "gold_doi": _doi(row["doi"]),
                        "doi": rec.doi, "related_doi": rec.related_doi, "confidence": rec.confidence,
                        "route": rec.resolved_via[0] if rec.resolved_via else None,
                        "outcome": outcome(row["doi"], rec)})
    counts = Counter(p["outcome"] for p in per_pdf)
    n = len(per_pdf)
    decided = counts["correct"] + counts["wrong"]
    by_route: Dict[str, Counter] = defaultdict(Counter)
    for p in per_pdf:
        if p["outcome"] in ("correct", "wrong"):
            by_route[p["route"]][p["outcome"]] += 1
    return {
        "n": n,
        "counts": {o: counts[o] for o in OUTCOMES},
        "precision": counts["correct"] / decided if decided else None,
        "unresolved_rate": (counts["missed"] + counts["correctly unresolved"]) / n if n else None,
        "by_route": {route: dict(c) for route, c in sorted(by_route.items())},
        "wrong": sorted(p["file"] for p in per_pdf if p["outcome"] == "wrong"),
        "absent": absent,                      # verified rows whose PDF is not in --pdfs
        "per_pdf": per_pdf,
    }


def changes(old: dict, new: dict) -> List[tuple]:
    """(file, old outcome, new outcome) for every PDF scored in both runs whose outcome changed."""
    before = {p["sha256"]: p["outcome"] for p in old["per_pdf"]}
    return [(p["file"], before[p["sha256"]], p["outcome"]) for p in new["per_pdf"]
            if p["sha256"] in before and before[p["sha256"]] != p["outcome"]]


# ---------------------------------------------------------------------------
# running pdfbatch
# ---------------------------------------------------------------------------

def _process(pdfs: Dict[str, Path], args) -> List[Record]:
    """process_pdf on each PDF, with the CLI's Settings and HTTP cache."""
    settings = Settings(openalex_api_key=args.openalex_key, grobid_url=args.grobid, llm=args.llm_tier)
    http = apis.Http(args.mailto, args.cache_dir / "http")

    def one(item):
        h, p = item
        print(f"  {p.name}", file=sys.stderr)
        return process_pdf(p, http, settings, h)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        return list(ex.map(one, pdfs.items()))


def _pdfs(folder: Path) -> Dict[str, Path]:
    """{sha256: path}; the hash is the key, so renamed files still match their gold row."""
    return {sha256_file(p): p for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix.lower() == ".pdf"}


def _read_gold(path: Path) -> List[dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:     # -sig: Excel saves CSV with a BOM
        return list(csv.DictReader(fh))


def _commit() -> str:
    try:
        git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, check=True,
                                        cwd=Path(__file__).parent).stdout.strip()
        return git("rev-parse", "--short", "HEAD") + ("-dirty" if git("status", "--porcelain", "--", "pdfbatch") else "")
    except (OSError, subprocess.CalledProcessError):
        return "nogit"


def init(args) -> int:
    """Add a row for every PDF not yet in the gold file, prefilled with what pdfbatch finds."""
    rows = _read_gold(args.gold) if args.gold.exists() else []
    known = {r["sha256"].strip() for r in rows}
    new = {h: p for h, p in _pdfs(args.pdfs).items() if h not in known}
    print(f"{len(new)} new PDF(s); {len(known)} already in {args.gold}", file=sys.stderr)
    if not new:
        return 0
    records = _process(new, args)
    fresh = not args.gold.exists() or args.gold.stat().st_size == 0
    if not fresh and not args.gold.read_bytes().endswith(b"\n"):
        with open(args.gold, "ab") as fh:
            fh.write(b"\r\n")
    with open(args.gold, "a", encoding="utf-8", newline="") as fh:   # append: never rewrite your edits
        w = csv.DictWriter(fh, FIELDS)
        if fresh:
            w.writeheader()
        for r in sorted(records, key=lambda r: Path(r.pdf_path).name.casefold()):
            w.writerow({"sha256": r.pdf_sha256, "file": Path(r.pdf_path).name,
                        "doi": (r.doi or "") if r.confidence != "unresolved" else "", "status": "unverified",
                        "comment": f"pdfbatch: {r.confidence} via {' / '.join(r.resolved_via)}"})
    print(f"Check each new row, correct its doi, and set status to verified: {args.gold}", file=sys.stderr)
    return 0


def _pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{100 * x:.1f}%"


def report(result: dict) -> None:
    n = result["n"]
    print(f"\n{n} verified PDF(s) scored")
    for o in OUTCOMES:
        c = result["counts"][o]
        print(f"  {o:<22}{c:>5}  {_pct(c / n if n else None):>6}")
    print(f"Precision (correct / (correct + wrong)): {_pct(result['precision'])}")
    print(f"Unresolved rate: {_pct(result['unresolved_rate'])}")
    if result["by_route"]:
        print("By route:")
        for route, c in result["by_route"].items():
            print(f"  {route:<28} correct {c.get('correct', 0):>4}   wrong {c.get('wrong', 0):>4}")
    if result["wrong"]:
        print("Wrong:")
        for f in result["wrong"]:
            print(f"  {f}")
    if result["absent"]:
        print(f"{len(result['absent'])} verified row(s) have no PDF in the folder: {', '.join(result['absent'])}")


def run(args) -> int:
    rows = [r for r in _read_gold(args.gold) if r.get("status", "").strip().lower() == "verified"]
    wanted = {r["sha256"].strip() for r in rows}
    pdfs = {h: p for h, p in _pdfs(args.pdfs).items() if h in wanted}
    print(f"{len(pdfs)} verified PDF(s) to process", file=sys.stderr)
    result = score(rows, _process(pdfs, args))
    report(result)

    commit = _commit()
    result.update(commit=commit, date=datetime.now().isoformat(timespec="seconds"),
                  options={"grobid": bool(args.grobid), "llm": args.llm})
    RESULTS.mkdir(exist_ok=True)
    stem = f"{datetime.now():%Y%m%d-%H%M%S}-{commit}"
    out = next(p for i in range(1, 100) if not (p := RESULTS / f"{stem}{f'-{i}' if i > 1 else ''}.json").exists())
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {out}")

    if args.compare:
        changed = changes(json.loads(args.compare.read_text(encoding="utf-8")), result)
        print(f"\n{len(changed)} PDF(s) changed outcome since {args.compare.name}")
        for f, before, after in changed:
            print(f"  {f}: {before} -> {after}")

    if args.max_wrong is not None and result["counts"]["wrong"] > args.max_wrong:
        print(f"\n{result['counts']['wrong']} wrong > --max-wrong {args.max_wrong}", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    load_dotenv(find_dotenv(usecwd=True))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name, helptext in (("init", "add new PDFs to the gold file, unverified"),
                           ("run", "score pdfbatch on the verified PDFs")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--pdfs", type=Path, required=True, help="folder of PDFs (searched recursively)")
        p.add_argument("--gold", type=Path, required=True, help="gold CSV (see bench/gold.example.csv)")
        p.add_argument("--mailto", default=os.environ.get("CONTACT_MAILTO"))
        p.add_argument("--openalex-key", default=os.environ.get("OPENALEX_API_KEY"))
        p.add_argument("--grobid", metavar="URL")
        p.add_argument("--llm", choices=["none", "anthropic", "ollama"], default="none")
        p.add_argument("--llm-model")
        p.add_argument("--ollama-url")
        p.add_argument("--workers", type=int, default=4)
        p.add_argument("--cache-dir", type=Path, help="default: the CLI's (env PDFBATCH_CACHE_DIR or the per-user cache)")
        if name == "run":
            p.add_argument("--compare", type=Path, metavar="RESULT.json", help="list PDFs whose outcome changed")
            p.add_argument("--max-wrong", type=int, metavar="N", help="exit 1 when more than N are wrong")
    args = ap.parse_args(argv)
    args.cache_dir = args.cache_dir or apis.default_cache_dir()
    if not args.mailto:
        ap.error("--mailto (or CONTACT_MAILTO) is required by the CrossRef/OpenAlex polite pools")
    if not args.pdfs.is_dir():
        ap.error(f"not a folder: {args.pdfs}")
    if args.command == "run" and not args.gold.exists():
        ap.error(f"no gold file: {args.gold} (create one with `init`)")
    try:
        args.llm_tier = LlmIdentifier(args.llm, args.llm_model, args.ollama_url) if args.llm != "none" else None
    except ValueError as e:                    # --llm anthropic without a key
        print(e, file=sys.stderr)
        return 2
    return init(args) if args.command == "init" else run(args)


if __name__ == "__main__":
    sys.exit(main())
