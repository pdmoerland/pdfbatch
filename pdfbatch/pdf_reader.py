"""Everything we pull out of the PDF itself, in one pass with PyMuPDF.

Nothing here touches the network or an LLM.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import List, Optional

import pymupdf  # PyMuPDF (AGPL); see README for the permissive alternative


@dataclass
class PdfContent:
    path: Path
    sha256: str
    info: dict                         # PDF Info dict (title, author, subject, keywords, …)
    xmp: str                           # raw XMP packet ('' if none)
    page_texts: List[str]              # text of every page
    header_footer_text: str            # page-1 text in top/bottom 12% bands + left/right margins
    title_guess: Optional[str]         # largest-font text on page 1
    link_uris: List[str] = field(default_factory=list)   # http(s) link annotations

    @property
    def first_pages(self) -> str:
        return "\n".join(self.page_texts[:2])

    @property
    def full_text(self) -> str:
        return "\n".join(self.page_texts)

    @property
    def is_probably_scanned(self) -> bool:
        return len(self.first_pages.strip()) < 40

    @cached_property
    def head(self) -> str:
        """First three pages plus the Info title, normalised for fuzzy matching."""
        return normalize("\n".join(self.page_texts[:3]) + " " + str(self.info.get("title", "")))


def normalize(s: Optional[str]) -> str:
    """Lower-case ASCII words: no accents, ligatures, hyphenated line breaks or punctuation."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"-\s*\n\s*", "", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_pdf(path: Path) -> PdfContent:
    doc = pymupdf.open(path)
    try:
        info = {k: v for k, v in (doc.metadata or {}).items() if v}
        try:
            xmp = doc.get_xml_metadata() or ""
        except Exception:                      # noqa: BLE001
            xmp = ""
        page_texts = [p.get_text("text") for p in doc]
        hf = _header_footer_text(doc[0]) if doc.page_count else ""
        title = _title_guess(doc[0]) if doc.page_count else None
        uris = [u for page in doc for ln in page.get_links()
                if (u := ln.get("uri") or "").startswith(("http://", "https://"))]
        return PdfContent(
            path=path,
            sha256=sha256_file(path),
            info=info,
            xmp=xmp,
            page_texts=page_texts,
            header_footer_text=hf,
            title_guess=title,
            link_uris=uris,
        )
    finally:
        doc.close()


def _header_footer_text(page) -> str:
    """Text in the bands where publishers print the article's own DOI
    (running header/footer, arXiv's left-margin stamp)."""
    r = page.rect
    bands = [
        pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * 0.12),
        pymupdf.Rect(r.x0, r.y1 - r.height * 0.12, r.x1, r.y1),
        pymupdf.Rect(r.x0, r.y0, r.x0 + r.width * 0.08, r.y1),
        pymupdf.Rect(r.x1 - r.width * 0.08, r.y0, r.x1, r.y1),
    ]
    return "\n".join(page.get_text("text", clip=b) for b in bands)


_TITLE_JUNK = re.compile(
    r"^(article|research article|original article|letter|review|report|open access|"
    r"check for updates|preprint|accepted manuscript|nature|science|cell|elife|plos)\b",
    re.I,
)


def _title_guess(page) -> Optional[str]:
    """Join the lines set in the largest font in the top 60% of page 1.

    Cheap and surprisingly reliable for journal PDFs; GROBID or the LLM tier
    handle the cases where it isn't.
    """
    d = page.get_text("dict")
    limit_y = page.rect.y0 + page.rect.height * 0.6
    lines = []  # (size, y0, text)
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            y0 = line["bbox"][1]
            if y0 > limit_y:
                continue
            size = max(s["size"] for s in spans)
            text = " ".join(s["text"].strip() for s in spans)
            lines.append((round(size, 1), y0, text))
    if not lines:
        return None
    for size in sorted({l[0] for l in lines}, reverse=True)[:3]:
        cand = [l for l in lines if abs(l[0] - size) < 0.6]
        cand.sort(key=lambda l: l[1])
        # Keep the first vertically contiguous run of lines at this size.
        run = [cand[0]]
        for l in cand[1:]:
            if l[1] - run[-1][1] <= size * 2.2:
                run.append(l)
            else:
                break
        text = " ".join(t for _, _, t in run)
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"-\s(?=[a-z])", "", text)    # de-hyphenate line breaks
        if 15 <= len(text) <= 350 and not _TITLE_JUNK.match(text) and re.search(r"[a-z]", text):
            return text
    return None


def abstract_from_text(text: str) -> Optional[str]:
    """Slice an abstract out of page-1/2 text when every API lacked one."""
    m = re.search(r"\b(?:abstract|summary)\b[\s:.\-—]*\n?", text, re.I)
    if not m:
        return None
    rest = text[m.end():]
    end = re.search(
        r"\n\s*(?:\d\.?\s*|I\.?\s+)?(?:introduction|background|keywords?|key\s*words|"
        r"main|results|significance|author summary)\b",
        rest, re.I,
    )
    chunk = rest[: end.start()] if end else rest[:2500]
    chunk = re.sub(r"-\n(?=[a-z])", "", chunk)
    chunk = re.sub(r"\s+", " ", chunk).strip()
    if 200 <= len(chunk) <= 4000:
        return chunk
    return None
