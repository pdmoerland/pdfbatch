"""Batch PDF -> metadata (.md + .ris + .json), DOI-first.

    pdf-batch ~/papers --mailto you@uni.nl --unpaywall

See README.md for every option.
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from . import apis
from .links import LinkChecker
from .llm import LlmIdentifier
from .pdf_reader import sha256_file
from .pipeline import Settings, process_pdf
from .record import Record
from .writers import read_json, write_json, write_markdown, write_ris


def _arguments(argv) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="pdf-batch", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input_dir", type=Path)
    ap.add_argument("--out", type=Path, help="output dir (default: <input_dir>/_metadata)")
    ap.add_argument("--name", default="papers", help="base name of output files (default: papers)")
    ap.add_argument("--recursive", action="store_true", help="include PDFs in subdirectories")
    ap.add_argument("--mailto", default=os.environ.get("CONTACT_MAILTO"),
                    help="contact e-mail for CrossRef/OpenAlex polite pools (env CONTACT_MAILTO)")
    ap.add_argument("--unpaywall", action="store_true",
                    help="also query Unpaywall for OA status (uses --mailto; needs a real address)")
    ap.add_argument("--openalex-key", default=os.environ.get("OPENALEX_API_KEY"))
    ap.add_argument("--prefer", choices=["held", "published"], default="held",
                    help="for preprint PDFs with a published version: cite the version you hold "
                         "(default) or the published one")
    ap.add_argument("--grobid", metavar="URL", help="GROBID server, e.g. http://localhost:8070")
    ap.add_argument("--llm", choices=["none", "anthropic", "ollama"], default="none",
                    help="last-resort LLM tier for unidentifiable PDFs (default: none)")
    ap.add_argument("--llm-model")
    ap.add_argument("--ollama-url")
    ap.add_argument("--check-links", action="store_true", help="HEAD-check code links")
    ap.add_argument("--preprint-ris-type", default="JOUR",
                    help="RIS TY for preprints: JOUR (default, imports cleanly everywhere) or UNPB")
    ap.add_argument("--attach", choices=["uri", "path", "none"], default="uri",
                    help="write L1 file attachment as file:// URI (default), plain path, or omit")
    ap.add_argument("--bom", action="store_true", help="write a UTF-8 BOM in the RIS file")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="re-process PDFs already in the JSON file")
    ap.add_argument("--cache-dir", type=Path, default=Path(".pdfbatch_cache"))
    args = ap.parse_args(argv)
    if not args.mailto:
        ap.error("--mailto (or CONTACT_MAILTO) is required by the CrossRef/OpenAlex polite pools")
    return args


def main(argv=None) -> int:
    load_dotenv(find_dotenv(usecwd=True))      # nearest .env from the cwd up
    args = _arguments(argv)
    log = lambda msg: print(msg, file=sys.stderr)

    src = args.input_dir.expanduser()
    if not src.is_dir():
        log(f"Not a folder: {str(src)!r}")    # repr shows stray control characters
        if '"' in str(src):                    # Windows reads `\"` as a literal quote
            log('Hint: remove the backslash before the closing quote ("...\\folder", not "...\\folder\\").')
        return 1
    src = src.resolve()
    out = (args.out or src / "_metadata").expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(p for p in (src.rglob("*") if args.recursive else src.iterdir())
                  if p.is_file() and p.suffix.lower() == ".pdf" and out not in p.parents)
    if not pdfs:
        log(f"No PDFs found in {src}")
        return 1

    llm = LlmIdentifier(args.llm, args.llm_model, args.ollama_url) if args.llm != "none" else None
    settings = Settings(
        unpaywall_email=args.mailto if args.unpaywall else None,
        openalex_api_key=args.openalex_key,
        prefer=args.prefer,
        grobid_url=args.grobid,
        llm=llm,
        link_checker=LinkChecker(args.cache_dir / "links") if args.check_links else None,
    )
    http = apis.Http(args.mailto, args.cache_dir / "http")

    # Reruns are incremental: PDFs are keyed by content, so renamed files are recognised.
    json_path = out / f"{args.name}.json"
    known = {} if args.force else {r.pdf_sha256: r for r in read_json(json_path)}
    records, todo = [], []
    for p in pdfs:
        if rec := known.get(sha256_file(p)):
            rec.pdf_path = str(p)
            records.append(rec)
        else:
            todo.append(p)
    log(f"{len(pdfs)} PDFs: {len(records)} cached, {len(todo)} to process")

    def process(p: Path) -> Record:
        try:
            return process_pdf(p, http, settings)
        except Exception as e:                 # noqa: BLE001 — never lose the whole batch
            return Record(pdf_path=str(p), title=p.stem, notes=[f"Crashed: {e!r}"])

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futures = {ex.submit(process, p): p for p in todo}
        for i, f in enumerate(as_completed(futures), 1):
            rec = f.result()
            records.append(rec)
            log(f"[{i}/{len(todo)}] {rec.confidence:<10} {rec.oa.status or '?':<8} "
                f"{rec.doi or '-':<35} {futures[f].name}")

    usage = ""
    if llm and llm.calls:
        usage = f"LLM fallback used for {llm.calls} PDF(s): {llm.tokens_in} input + {llm.tokens_out} output tokens."
        log(usage)

    write_json(records, json_path)
    write_markdown(records, out / f"{args.name}.md", llm_usage=usage)
    write_ris(records, out / f"{args.name}.ris", preprint_type=args.preprint_ris_type,
              attach=args.attach, bom=args.bom)
    log(f"Wrote {out}/{args.name}.{{md,ris,json}}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
