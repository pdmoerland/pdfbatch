"""Renderers: results.json (source of truth) → papers.ris + papers.md."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Iterable, List

from .record import Record

from .citation import format_authors_apa, to_sentence_case

RIS_TYPES = {
    "journal-article": "JOUR",
    "book-chapter": "CHAP",
    "book-section": "CHAP",
    "book-part": "CHAP",
    "book": "BOOK",
    "monograph": "BOOK",
    "edited-book": "EDBOOK",
    "reference-book": "BOOK",
    "proceedings-article": "CPAPER",
    "dissertation": "THES",
    "report": "RPRT",
    "dataset": "DATA",
    "standard": "STAND",
    "posted-content": "JOUR",
}


def _one_line(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------------------
# RIS
# ---------------------------------------------------------------------------

def ris_entry(r: Record, *, preprint_type: str = "JOUR", attach: str = "uri") -> str:
    lines: List[str] = []
    add = lambda tag, val: lines.append(f"{tag}  - {_one_line(str(val))}") if val not in (None, "", []) else None

    ty = preprint_type if r.is_preprint else RIS_TYPES.get(r.item_type, "JOUR")
    add("TY", ty)
    for a in r.authors:
        add("AU", a.ris())
    add("TI", r.title)
    add("T2", r.journal)
    add("J2", r.journal_abbrev)
    add("PY", r.year)
    if r.date:
        parts = r.date.split("-")
        add("DA", "/".join(parts + [""] * (3 - len(parts))) + "/")   # YYYY/MM/DD/
    add("VL", r.volume)
    add("IS", r.issue)
    if r.first_page:
        add("SP", r.first_page)
        add("EP", r.last_page)
    elif r.article_number:
        add("SP", r.article_number)
        add("M1", r.article_number)          # EndNote: Article Number
    add("SN", r.issn)
    add("PB", r.publisher)
    add("DO", r.doi)
    add("UR", f"https://doi.org/{r.doi}" if r.doi else (r.url or r.oa.url))
    if r.oa.url and r.oa.url != (f"https://doi.org/{r.doi}" if r.doi else r.url):
        add("UR", r.oa.url)
    if r.pmid:
        add("AN", f"PMID:{r.pmid}")
    add("AB", r.abstract)

    # Keywords become tags in Zotero and keywords in EndNote → filterable.
    kws = []
    if r.oa.status:
        kws.append(f"OA: {r.oa.status}")
        kws.append("Open access" if r.oa.is_oa else "Closed access")
    if r.oa.license:
        kws.append(f"License: {r.oa.license}")
    if any(l.kind == "code" for l in r.links):
        kws.append("Has code")
    if any(l.kind == "data" for l in r.links):
        kws.append("Has data")
    if r.confidence in ("low", "unresolved"):
        kws.append("pdfbatch: needs review")
    for k in kws:
        add("KW", k)

    notes = []
    if r.oa.status:
        oa = f"Open access: {r.oa.status}"
        if r.oa.license:
            oa += f", {r.oa.license}"
        if r.oa.version:
            oa += f", {r.oa.version}"
        if r.oa.url:
            oa += f" ({r.oa.url})"
        notes.append(oa + f" [via {r.oa.source}]")
    for l in r.links:
        notes.append(f"{l.kind.capitalize()}{f' ({l.label})' if l.label else ''}: {l.url}")
    notes.extend(r.notes)
    notes.append(f"pdfbatch: {r.confidence} confidence via {' > '.join(r.resolved_via)}")
    add("N1", " | ".join(notes))

    if r.pdf_path and attach != "none":
        p = Path(r.pdf_path)
        add("L1", p.as_uri() if attach == "uri" else str(p))
    lines.append("ER  - ")
    return "\r\n".join(lines) + "\r\n\r\n"


def write_ris(records: Iterable[Record], path: Path, *, preprint_type: str = "JOUR",
              attach: str = "uri", bom: bool = False) -> None:
    text = "".join(ris_entry(r, preprint_type=preprint_type, attach=attach)
                   for r in sorted(records, key=sort_key))
    path.write_text(("\ufeff" if bom else "") + text, encoding="utf-8", newline="")


# ---------------------------------------------------------------------------
# APA (the repo's formatter lacks volume/issue/pages, so this extends it)
# ---------------------------------------------------------------------------

def apa(r: Record) -> str:
    names = [a.ris() for a in r.authors] if r.authors else []
    who = format_authors_apa(names) if names else ""
    title = to_sentence_case(_one_line(r.title or "")).rstrip(".")
    year = f"({r.year})." if r.year else "(n.d.)."
    src = ""
    if r.journal:
        src = f"*{r.journal}*"
        if r.volume:
            src += f", *{r.volume}*"
            if r.issue:
                src += f"({r.issue})"
        if r.first_page:
            src += f", {r.first_page}" + (f"–{r.last_page}" if r.last_page else "")
        elif r.article_number:
            src += f", {r.article_number}"
        src += "."
    link = f"https://doi.org/{r.doi}" if r.doi else (r.url or "")
    if not who:                       # APA: title moves to the author position
        return " ".join(x for x in (f"{title}." if title else "", year, src, link) if x)
    return " ".join(x for x in (who, year, f"{title}." if title else "", src, link) if x)


def sort_key(r: Record):
    return ((r.first_author_family() or "~").lower(), r.year or "", (r.title or "").lower())


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def write_markdown(records: List[Record], path: Path, *, llm_usage: str = "") -> None:
    recs = sorted(records, key=sort_key)
    n_oa = sum(1 for r in recs if r.oa.is_oa)
    review = [r for r in recs if r.confidence in ("low", "unresolved")]
    out: List[str] = []
    out.append("# Paper metadata")
    out.append("")
    out.append(f"Generated {dt.datetime.now():%Y-%m-%d %H:%M} from {len(recs)} PDFs. "
               f"{n_oa} open access, {len(recs) - n_oa} closed or unknown, "
               f"{len(review)} need review.{(' ' + llm_usage) if llm_usage else ''}")
    out.append("")

    if review:
        out.append("## Needs review")
        out.append("")
        for r in review:
            out.append(f"- `{Path(r.pdf_path).name}` — {r.confidence}: {'; '.join(r.notes) or 'weak match'}")
        out.append("")

    out.append("## Papers")
    out.append("")
    for r in recs:
        out.append(f"**File:** [`{Path(r.pdf_path).name}`]({Path(r.pdf_path).as_uri()})")
        out.append("")
        out.append(f"> {apa(r)}")
        out.append("")
        if r.doi:
            out.append(f"**DOI:** [{r.doi}](https://doi.org/{r.doi})  ")
        ids = [x for x in (f"arXiv:{r.arxiv_id}" if r.arxiv_id else "",
                           f"PMID {r.pmid}" if r.pmid else "", r.pmcid or "") if x]
        if ids:
            out.append(f"**Other IDs:** {', '.join(ids)}  ")
        extra = ", ".join(x for x in (r.oa.license, r.oa.version) if x)
        oa_line = f"**Open access:** {r.oa.status or 'unknown'}{f' ({extra})' if extra else ''}"
        if r.oa.url:
            oa_line += f" — [free copy]({r.oa.url})"
        out.append(oa_line + "  ")
        for kind in ("code", "data"):
            ls = [l for l in r.links if l.kind == kind]
            if ls:
                out.append(f"**{kind.capitalize()}:** " + ", ".join(
                    f"[{l.url.split('://', 1)[-1]}]({l.url}){f' — {l.label}' if l.label else ''}" for l in ls) + "  ")
        out.append(f"**Resolved:** {r.confidence} confidence via {' → '.join(r.resolved_via)}"
                   + (f"; abstract from {r.abstract_source}" if r.abstract_source else ""))
        for n in r.notes:
            out.append(f"\n⚠️ {n}")
        out.append("")
        if r.abstract:
            out.append("<details><summary>Abstract</summary>")
            out.append("")
            out.append(_one_line(r.abstract))
            out.append("")
            out.append("</details>")
            out.append("")
        out.append("---")
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def write_json(records: List[Record], path: Path) -> None:
    path.write_text(json.dumps([r.to_dict() for r in records], indent=2, ensure_ascii=False),
                    encoding="utf-8")


def read_json(path: Path) -> List[Record]:
    if not path.exists():
        return []
    try:
        return [Record.from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]
    except (ValueError, TypeError):
        return []
