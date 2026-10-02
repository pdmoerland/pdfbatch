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

from . import apis
from .llm import LlmIdentifier
from .pdf_reader import sha256_file
from .pipeline import Settings, process_pdf
from .writers import read_json, write_json, write_markdown, write_ris


def main(argv=None) -> int:
    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))   # nearest .env from the cwd up
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
    ap.add_argument("--force", action="store_true", help="re-process PDFs already in results.json")
    ap.add_argument("--cache-dir", type=Path, default=Path(".pdfbatch_cache"))
    args = ap.parse_args(argv)

    if not args.mailto:
        ap.error("--mailto (or CONTACT_MAILTO) is required by the CrossRef/OpenAlex polite pools")
    src = args.input_dir.expanduser().resolve()
    out = (args.out or src / "_metadata").expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(p for p in (src.rglob("*") if args.recursive else src.iterdir())
                  if p.is_file() and p.suffix.lower() == ".pdf" and out not in p.parents)
    if not pdfs:
        print(f"No PDFs found in {src}", file=sys.stderr)
        return 1

    llm = None
    if args.llm != "none":
        llm = LlmIdentifier(args.llm, args.llm_model, args.ollama_url)
    validator = None
    if args.check_links:
        from .link_validator import LinkValidator
        validator = LinkValidator(cache_dir=args.cache_dir / "links")

    settings = Settings(
        mailto=args.mailto,
        unpaywall_email=args.mailto if args.unpaywall else None,
        openalex_api_key=args.openalex_key,
        prefer=args.prefer,
        grobid_url=args.grobid,
        llm=llm,
        link_validator=validator,
    )
    http = apis.Http(args.mailto, args.cache_dir / "http")

    json_path = out / f"{args.name}.json"
    previous = {r.pdf_sha256: r for r in read_json(json_path)} if not args.force else {}

    records, todo = [], []
    for p in pdfs:
        sha = sha256_file(p)
        if sha in previous:
            rec = previous[sha]
            rec.pdf_path = str(p)            # file may have been renamed or moved
            records.append(rec)
        else:
            todo.append(p)
    print(f"{len(pdfs)} PDFs: {len(records)} cached, {len(todo)} to process", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(process_pdf, p, http, settings): p for p in todo}
        for i, f in enumerate(as_completed(futs), 1):
            p = futs[f]
            try:
                rec = f.result()
            except Exception as e:           # noqa: BLE001 — never lose the whole batch
                from .record import Record
                rec = Record(pdf_path=str(p), title=p.stem, notes=[f"Crashed: {e!r}"])
            records.append(rec)
            print(f"[{i}/{len(todo)}] {rec.confidence:<10} {rec.oa.status or '?':<8} "
                  f"{(rec.doi or '-'):<35} {p.name}", file=sys.stderr)

    usage = ""
    if llm and llm.calls:
        usage = f"LLM fallback used for {llm.calls} PDF(s): {llm.tokens_in} input + {llm.tokens_out} output tokens."
        print(usage, file=sys.stderr)

    write_json(records, json_path)
    write_markdown(records, out / f"{args.name}.md", llm_usage=usage)
    write_ris(records, out / f"{args.name}.ris", preprint_type=args.preprint_ris_type,
              attach=args.attach if args.attach != "none" else "none", bom=args.bom)
    print(f"Wrote {out}/{args.name}.{{md,ris,json}}", file=sys.stderr)
    return 0


