"""Renderers: records → papers.json (source of truth), papers.ris and papers.md."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Iterable, List

from .record import Author, Record

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


def sort_key(r: Record):
    first = (r.authors[0].family or r.authors[0].literal) if r.authors else ""
    return ((first or "~").lower(), r.year or "", (r.title or "").lower())


def _oa_details(r: Record) -> str:
    """'cc-by, publishedVersion', or as much of it as is known."""
    return ", ".join(x for x in (r.oa.license, r.oa.version) if x)


# ---------------------------------------------------------------------------
# APA 7
# ---------------------------------------------------------------------------

def _apa_name(a: Author) -> str:
    """'van der Berg, P.'; organisations stay as written."""
    if not a.family:
        return a.literal
    initials = " ".join(f"{g[0]}." for g in a.given.split())
    return f"{a.family}, {initials}" if initials else a.family


def _apa_authors(authors: List[Author]) -> str:
    names = [_apa_name(a) for a in authors]
    if len(names) <= 1:
        return "".join(names)
    if len(names) > 20:                        # first 19, an ellipsis, the last
        return ", ".join(names[:19]) + ", ... " + names[-1]
    return ", ".join(names[:-1]) + ", & " + names[-1]


def _sentence_case(title: str) -> str:
    """Lower-case every word but the first, acronyms ('DNA') and mixed case ('iPSC', 'GPT-4')."""
    return " ".join(w if i == 0 or (w.isupper() and len(w) > 1) or any(c.isupper() for c in w[1:])
                    else w.lower() for i, w in enumerate(title.split()))


def apa(r: Record) -> str:
    title = _sentence_case(_one_line(r.title)).rstrip(".")
    title = f"{title}." if title else ""
    source = ""
    if r.journal:
        source = f"*{r.journal}*"
        if r.volume:
            source += f", *{r.volume}*" + (f"({r.issue})" if r.issue else "")
        if r.first_page:
            source += f", {r.first_page}" + (f"–{r.last_page}" if r.last_page else "")
        elif r.article_number:
            source += f", {r.article_number}"
        source += "."
    who = _apa_authors(r.authors)
    year = f"({r.year})." if r.year else "(n.d.)."
    link = f"https://doi.org/{r.doi}" if r.doi else r.url
    parts = (who, year, title, source, link) if who else (title, year, source, link)   # no author: title leads
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# RIS
# ---------------------------------------------------------------------------

def ris_entry(r: Record, *, preprint_type: str = "JOUR", attach: str = "uri") -> str:
    lines: List[str] = []

    def add(tag: str, val) -> None:
        if val not in (None, "", []):
            lines.append(f"{tag}  - {_one_line(str(val))}")

    add("TY", preprint_type if r.is_preprint else RIS_TYPES.get(r.item_type, "JOUR"))
    for a in r.authors:
        add("AU", a.ris())
    add("TI", r.title)
    add("T2", r.journal)
    add("J2", r.journal_abbrev)
    add("PY", r.year)
    if r.date:
        parts = r.date.split("-")
        add("DA", "/".join(parts + [""] * (3 - len(parts))) + "/")      # YYYY/MM/DD/
    add("VL", r.volume)
    add("IS", r.issue)
    if r.first_page:
        add("SP", r.first_page)
        add("EP", r.last_page)
    elif r.article_number:
        add("SP", r.article_number)
        add("M1", r.article_number)            # EndNote: Article Number
    add("SN", r.issn)
    add("PB", r.publisher)
    add("DO", r.doi)
    for url in dict.fromkeys(u for u in (f"https://doi.org/{r.doi}" if r.doi else r.url, r.oa.url) if u):
        add("UR", url)
    if r.pmid:
        add("AN", f"PMID:{r.pmid}")
    add("AB", r.abstract)

    # Keywords become tags in Zotero and keywords in EndNote, so they can be filtered on.
    if r.oa.status:
        add("KW", f"OA: {r.oa.status}")
        add("KW", "Open access" if r.oa.is_oa else "Closed access")
    if r.oa.license:
        add("KW", f"License: {r.oa.license}")
    for kind in ("code", "data"):
        if any(l.kind == kind for l in r.links):
            add("KW", f"Has {kind}")
    if r.needs_review:
        add("KW", "pdfbatch: needs review")

    notes = []
    if r.oa.status:
        details = _oa_details(r)
        notes.append(f"Open access: {r.oa.status}" + (f", {details}" if details else "")
                     + (f" ({r.oa.url})" if r.oa.url else "") + f" [via {r.oa.source}]")
    notes += [f"{l.kind.capitalize()}{f' ({l.label})' if l.label else ''}: {l.url}" for l in r.links]
    notes += r.notes
    notes.append(f"pdfbatch: {r.confidence} confidence via {' > '.join(r.resolved_via)}")
    add("N1", " | ".join(notes))

    if r.pdf_path and attach != "none":
        add("L1", Path(r.pdf_path).as_uri() if attach == "uri" else r.pdf_path)
    lines.append("ER  - ")
    return "\r\n".join(lines) + "\r\n\r\n"


def write_ris(records: Iterable[Record], path: Path, *, preprint_type: str = "JOUR",
              attach: str = "uri", bom: bool = False) -> None:
    text = "".join(ris_entry(r, preprint_type=preprint_type, attach=attach)
                   for r in sorted(records, key=sort_key))
    path.write_text(("﻿" if bom else "") + text, encoding="utf-8", newline="")


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _md_entry(r: Record) -> List[str]:
    pdf = Path(r.pdf_path)
    out = [f"**File:** [`{pdf.name}`]({pdf.as_uri()})", "", f"> {apa(r)}", ""]
    if r.doi:
        out.append(f"**DOI:** [{r.doi}](https://doi.org/{r.doi})  ")
    ids = [x for x in (r.arxiv_id and f"arXiv:{r.arxiv_id}", r.pmid and f"PMID {r.pmid}", r.pmcid) if x]
    if ids:
        out.append(f"**Other IDs:** {', '.join(ids)}  ")
    details = _oa_details(r)
    out.append(f"**Open access:** {r.oa.status or 'unknown'}" + (f" ({details})" if details else "")
               + (f" — [free copy]({r.oa.url})" if r.oa.url else "") + "  ")
    for kind in ("code", "data"):
        if ls := [l for l in r.links if l.kind == kind]:
            out.append(f"**{kind.capitalize()}:** " + ", ".join(
                f"[{l.url.split('://', 1)[-1]}]({l.url})" + (f" — {l.label}" if l.label else "") for l in ls) + "  ")
    out.append(f"**Resolved:** {r.confidence} confidence via {' → '.join(r.resolved_via)}")
    out += [f"\n⚠️ {n}" for n in r.notes]
    return out + ["", "---", ""]


def write_markdown(records: List[Record], path: Path, *, llm_usage: str = "") -> None:
    recs = sorted(records, key=sort_key)
    n_oa = sum(1 for r in recs if r.oa.is_oa)
    review = [r for r in recs if r.needs_review]
    out = ["# Paper metadata", "",
           f"Generated {dt.datetime.now():%Y-%m-%d %H:%M} from {len(recs)} PDFs. "
           f"{n_oa} open access, {len(recs) - n_oa} closed or unknown, {len(review)} need review."
           + (f" {llm_usage}" if llm_usage else ""), ""]
    if review:
        out += ["## Needs review", ""]
        out += [f"- `{Path(r.pdf_path).name}` — {r.confidence}: {'; '.join(r.notes) or 'weak match'}"
                for r in review]
        out.append("")
    out += ["## Papers", ""]
    for r in recs:
        out += _md_entry(r)
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
