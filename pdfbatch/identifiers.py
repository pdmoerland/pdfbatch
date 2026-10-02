"""Find the PDF's *own* DOI / arXiv ID, ranked by how trustworthy the location is.

The main hazard is picking up a DOI from the reference list or a
"see also" box, so position matters more than frequency. Every candidate is
later checked against the PDF text (pipeline.validate) before it is accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from .pdf_reader import PdfContent

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>{}]+)", re.I)
ARXIV_NEW_RE = re.compile(r"arXiv\s*:\s*(\d{4}\.\d{4,5})(v\d+)?", re.I)
ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(v\d+)?", re.I)
ARXIV_OLD_RE = re.compile(r"arXiv\s*:\s*([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)
REFS_HEADING_RE = re.compile(r"^\s*(references|bibliography|literature cited|works cited)\s*$", re.I | re.M)

# DOIs that are never the article itself.
_NOT_ARTICLE = re.compile(
    r"^10\.(5281/zenodo|6084/m9\.figshare|5061/dryad|17605/osf|7303/syn|48550/arxiv)", re.I
)


@dataclass
class Candidate:
    kind: str        # 'doi' | 'arxiv'
    value: str
    source: str      # where we found it
    score: float


def clean_doi(raw: str) -> Optional[str]:
    d = raw.strip()
    d = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", d, flags=re.I)
    # Trailing punctuation and unbalanced closing brackets.
    while d and d[-1] in ".,;:'\"”’>]}":
        d = d[:-1]
    while d.endswith(")") and d.count("(") < d.count(")"):
        d = d[:-1]
    # Publisher URL suffixes that leak into text.
    d = re.sub(r"/(?:full|abstract|pdf|epdf|suppl_file.*)$", "", d, flags=re.I)
    d = re.sub(r"\.(?:pdf|html?)$", "", d, flags=re.I)
    # bioRxiv/medRxiv version suffix: 10.1101/2023.01.01.522000v2 → drop vN
    d = re.sub(r"^(10\.1101/\d{4}\.\d{2}\.\d{2}\.\d+)v\d+$", r"\1", d)
    if not re.match(r"^10\.\d{4,9}/\S+$", d):
        return None
    return d.lower()


def _dois_in(text: str) -> List[str]:
    out = []
    # Re-join DOIs split at a line break right after the slash or a hyphen.
    text = re.sub(r"(10\.\d{4,9}/)\s*\n\s*", r"\1", text)
    for m in DOI_RE.finditer(text):
        d = clean_doi(m.group(1))
        if d and not _NOT_ARTICLE.match(d) and d not in out:
            out.append(d)
    return out


def _arxiv_in(text: str) -> List[str]:
    out = []
    for rx in (ARXIV_NEW_RE, ARXIV_URL_RE, ARXIV_OLD_RE):
        for m in rx.finditer(text):
            if m.group(1) not in out:
                out.append(m.group(1))
    return out


def _xmp_dois(xmp: str) -> List[str]:
    out = []
    for tag in ("prism:doi", "dc:identifier", "pdfx:doi", "crossmark:DOI", "prism:url"):
        for m in re.finditer(rf"<{tag}[^>]*>(.*?)</{tag}>", xmp, re.S | re.I):
            out.extend(_dois_in(m.group(1)))
        for m in re.finditer(rf'{tag}="([^"]+)"', xmp, re.I):
            out.extend(_dois_in(m.group(1)))
    return list(dict.fromkeys(out))


def _before_references(text: str) -> str:
    m = REFS_HEADING_RE.search(text)
    return text[: m.start()] if m else text


def find_candidates(pdf: PdfContent) -> List[Candidate]:
    cands: List[Candidate] = []

    def add(kind, value, source, score):
        for c in cands:
            if c.kind == kind and c.value == value:
                c.score = max(c.score, score) + 0.05      # seen in several places
                return
        cands.append(Candidate(kind, value, source, score))

    for d in _xmp_dois(pdf.xmp):
        add("doi", d, "xmp", 1.0)
    info_blob = " ".join(str(pdf.info.get(k, "")) for k in ("subject", "keywords", "title"))
    for d in _dois_in(info_blob):
        add("doi", d, "pdf-info", 0.95)

    for d in _dois_in(pdf.header_footer_text):
        add("doi", d, "page1-header", 0.9)
    for a in _arxiv_in(pdf.header_footer_text):
        add("arxiv", a, "page1-margin", 0.9)

    p1 = _before_references(pdf.page_texts[0]) if pdf.page_texts else ""
    for i, d in enumerate(_dois_in(p1)):
        add("doi", d, "page1", 0.8 - 0.1 * i)          # the first DOI on page 1 is usually ours
    for a in _arxiv_in(p1):
        add("arxiv", a, "page1", 0.75)

    if len(pdf.page_texts) > 1:
        p2 = _before_references(pdf.page_texts[1])
        for i, d in enumerate(_dois_in(p2)[:3]):
            add("doi", d, "page2", 0.5 - 0.1 * i)
    if len(pdf.page_texts) > 2:
        # Some publishers (e.g. older Elsevier, some OUP) print the DOI only at the end.
        last = pdf.page_texts[-1]
        if not REFS_HEADING_RE.search(last):
            for d in _dois_in(last[-1500:])[:2]:
                add("doi", d, "last-page", 0.3)

    cands.sort(key=lambda c: c.score, reverse=True)
    return cands
