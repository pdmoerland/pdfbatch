"""Everything we pull out of the PDF itself, in one pass with PyMuPDF.

Nothing here touches the network or an LLM.
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
import threading
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
    title_guesses: List[str]           # large-font text runs on page 1, largest first
    link_uris: List[str] = field(default_factory=list)   # http(s) link annotations
    ocr: bool = False                  # page-1 text came from OCR (scan without a text layer)

    @property
    def title_guess(self) -> Optional[str]:
        return self.title_guesses[0] if self.title_guesses else None

    @property
    def first_pages(self) -> str:
        return "\n".join(self.page_texts[:2])

    @property
    def full_text(self) -> str:
        return "\n".join(self.page_texts)

    @property
    def is_probably_scanned(self) -> bool:
        return not self.page_texts or unreadable(self.page_texts[0])

    @property
    def opening(self) -> Optional[str]:
        """The first two substantial lines of page 1, joined: in reading order the title usually
        comes first. A last-resort search query for when font sizes mislead, as they do in OCR
        output, where sizes are estimated from the scanned glyphs."""
        lines = [" ".join(l.split()) for l in (self.page_texts[0] if self.page_texts else "").splitlines()]
        return " ".join([l for l in lines if len(l) >= 15][:2]) or None

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


def unreadable(text: str) -> bool:
    """No usable text layer: (almost) no text, as in a scan, or text in a font without a
    character map, which comes out as control characters (NUL, STX, SOH, …)."""
    chars = "".join(text.split())
    return len(chars) < 40 or sum(ord(c) < 32 for c in chars) > 0.01 * len(chars)


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
        hf, titles, ocr = "", [], False
        if doc.page_count:
            page = doc[0]
            tp = page.get_textpage(flags=pymupdf.TEXTFLAGS_TEXT)
            if unreadable(page_texts[0]) and (ocr_tp := _ocr(page)):
                tp, ocr = ocr_tp, True
                page_texts[0] = tp.extractText()
            hf = _header_footer_text(tp, page.rect)
            titles = _title_guesses(tp, page.rect)
        uris = [u for page in doc for ln in page.get_links()
                if (u := ln.get("uri") or "").startswith(("http://", "https://"))]
        return PdfContent(
            path=path,
            sha256=sha256_file(path),
            info=info,
            xmp=xmp,
            page_texts=page_texts,
            header_footer_text=hf,
            title_guesses=titles,
            link_uris=uris,
            ocr=ocr,
        )
    finally:
        doc.close()


# Where the usual installers put Tesseract's language files. The Windows installer does not
# add Tesseract to PATH, which is how PyMuPDF looks for it (besides TESSDATA_PREFIX).
_TESSDATA_DIRS = (
    r"C:\Program Files\Tesseract-OCR\tessdata",
    r"C:\Program Files (x86)\Tesseract-OCR\tessdata",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tessdata"),
    "/opt/homebrew/share/tessdata",
    "/usr/local/share/tessdata",
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
)


# Leptonica, Tesseract's image library, refuses use from two threads at once
# ("Attempt to use Leptonica from 2 threads at once!"); OCR one page at a time.
_OCR_LOCK = threading.Lock()


@functools.cache
def _tessdata() -> Optional[str]:
    try:
        return pymupdf.get_tessdata()          # TESSDATA_PREFIX, or a tesseract on PATH
    except RuntimeError:
        return next((d for d in _TESSDATA_DIRS if Path(d, "eng.traineddata").is_file()), None)


def _ocr(page) -> Optional["pymupdf.TextPage"]:
    """Page text by OCR, for scans without a text layer; None when Tesseract is not installed.

    Only page 1 is read: it carries the title, authors and often a citation block
    (JSTOR cover pages), which is all identification needs.
    """
    tessdata = _tessdata()
    if not tessdata:
        return None
    try:
        with _OCR_LOCK:
            return page.get_textpage_ocr(flags=pymupdf.TEXTFLAGS_TEXT, language="eng", dpi=300, full=True,
                                         tessdata=tessdata)
    except (RuntimeError, pymupdf.mupdf.FzErrorBase):    # a failed OCR leaves the PDF unread, not unopened
        return None


def _header_footer_text(tp, r) -> str:
    """Text in the bands where publishers print the article's own DOI
    (running header/footer, arXiv's left-margin stamp)."""
    bands = [
        pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * 0.12),
        pymupdf.Rect(r.x0, r.y1 - r.height * 0.12, r.x1, r.y1),
        pymupdf.Rect(r.x0, r.y0, r.x0 + r.width * 0.08, r.y1),
        pymupdf.Rect(r.x1 - r.width * 0.08, r.y0, r.x1, r.y1),
    ]
    return "\n".join(tp.extractTextbox(b) for b in bands)


_TITLE_JUNK = re.compile(
    r"^(article|research article|original article|letter|review|report|open access|"
    r"check for updates|preprint|accepted manuscript|nature|science|cell|elife|plos)\b",
    re.I,
)


def _title_guesses(tp, rect) -> List[str]:
    """Runs of lines set in the three largest fonts in the top 60% of page 1, largest first.

    Usually the first is the title. Not always: a series banner ("… Working Paper
    Series") can be set larger, so later runs are kept too and the title search
    tries each in turn; validation rejects the wrong ones.
    """
    d = tp.extractDICT()
    limit_y = rect.y0 + rect.height * 0.6
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
            # Leave out superscripts (footnote markers, affiliation numbers): "Microarray¹" is not
            # titled "Microarray 1", and the stray digit alone can make a title search miss.
            text = " ".join(s["text"].strip() for s in spans if s["size"] >= 0.7 * size)
            lines.append((round(size, 1), y0, text))
    guesses = []
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
        # Three real words rather than "has lower case": theses and older papers set titles in
        # capitals, while short banners ("NATURE GENETICS", letter-spaced "A R T I C L E S") are not titles.
        words = sum(len(w) > 1 for w in text.split())
        if 15 <= len(text) <= 350 and not _TITLE_JUNK.match(text) and words >= 3:
            guesses.append(text)
    return guesses


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
