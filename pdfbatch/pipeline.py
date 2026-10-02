"""Per-PDF pipeline.

  PDF ──► identifier tiers (0 tokens) ──► resolver chain ──► validation
     │        1 XMP / Info dict              CrossRef or arXiv
     │        2 regex: header/margins,       + OpenAlex  (fields + OA)
     │          page 1, page 2, last page    + Europe PMC (abstract, PMID)
     │        3 title guesses → CrossRef/OpenAlex search
     │        4 GROBID header (optional)
     │        5 LLM search key (optional, ~1.5k tokens)
     └──► OA (Unpaywall ▸ OpenAlex ▸ preprint-server rule)
     └──► code & data links from the full text

Specificity over sensitivity: a record is applied only when the PDF confirms it, by
title *and* authors. Otherwise the PDF stays unresolved and is flagged for review;
a missing record costs a minute by hand, a wrong one may never be noticed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Tuple

import requests
from rapidfuzz import fuzz, process
from rapidfuzz.distance import Levenshtein

from . import apis
from .identifiers import clean_doi, find_candidates
from .links import LinkChecker, find_links
from .llm import LlmIdentifier
from .pdf_reader import PdfContent, abstract_from_text, normalize, read_pdf
from .record import Author, OpenAccess, Record

PREPRINT_DOI_PREFIXES = ("10.1101/", "10.48550/", "10.21203/", "10.20944/", "10.31234/",
                         "10.31219/", "10.36227/", "10.22541/", "10.2139/ssrn")

Hit = Tuple[Optional[dict], str, str]          # (API fields, confidence, how it was found)


@dataclass
class Settings:
    unpaywall_email: Optional[str] = None
    openalex_api_key: Optional[str] = None
    prefer: str = "held"                       # 'held' | 'published'
    grobid_url: Optional[str] = None
    llm: Optional[LlmIdentifier] = None
    link_checker: Optional[LinkChecker] = None


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

_RANK = {"low": 0, "medium": 1, "high": 2}


def _good(confidence: str) -> bool:
    return _RANK[confidence] >= _RANK["medium"]


def _name_in(pdf: PdfContent, name: str) -> bool:
    """A (normalised) family name occurs in the PDF.

    Letters-only boundaries, because affiliation numbers stick to names in the text
    ("segal1"). Source records have typos and truncations ("Patterso" for Patterson),
    so long names may differ by one character (8–11 letters) or two (12+), word by
    word. Shorter names must match exactly: one edit away, "Chen" is "then" and a
    reviewer called "States" is "stages".
    """
    if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", pdf.head):
        return True
    return all(process.extractOne(w, pdf.words, scorer=Levenshtein.distance,
                                  score_cutoff=0 if len(w) < 8 else 1 if len(w) < 12 else 2) is not None
               for w in name.split())


def validate(f: dict, pdf: PdfContent, *, all_authors: bool = False) -> Tuple[str, float]:
    """Does the API record describe *this* PDF? → (confidence, title score).

    `high`: the title is found (≥ 90) and so are the authors; `medium`: the title nearly
    (≥ 75) and the authors; otherwise `low`. "The authors" means one of the first five,
    or with `all_authors` every one: for search hits, whose generic title ("Model
    Assessment and Selection") can belong to many works. A title alone never confirms a
    match, and neither does a title under three words ("32", "NCI & FDA").
    """
    title = normalize(f.get("title"))
    score = fuzz.partial_ratio(title, pdf.head) if pdf.head and len(title.split()) >= 3 else 0.0
    found = [_name_in(pdf, s)
             for s in (normalize(a.family or a.literal.split(" ")[-1]) for a in f.get("authors") or [])
             if len(s) > 1]                    # a one-letter "family name" is a data error, not checkable
    named = bool(found) and (all(found) if all_authors else any(found[:5]))
    if named and score >= 90:
        return "high", score
    if named and score >= 75:
        return "medium", score
    return "low", score


# ---------------------------------------------------------------------------
# identification
# ---------------------------------------------------------------------------

def _resolve(http: apis.Http, s: Settings, kind: str, value: str) -> Optional[dict]:
    """API fields for a DOI or arXiv ID, with `_via` naming the API that answered."""
    if kind == "arxiv":
        f, via = apis.arxiv_work(http, value), "arxiv"
    else:
        f, via = apis.crossref_work(http, value), "crossref"
        if not f:                              # DataCite DOIs (arXiv, Zenodo, …)
            f, via = apis.openalex_work(http, value, s.openalex_api_key), "openalex"
    if f:
        f["_via"] = via
    return f


def _resembles(query: str, title: Optional[str]) -> bool:
    """A search hit's title resembles the title searched for, and is not a mere fragment of it.

    A hit titled after a phrase in the query ("Surface-Enhanced Laser Desorption/Ionization
    Time of Flight Mass Spectrometry", an encyclopedia entry) shares its words but covers
    too little of it. A longer hit is fine: OCR'd title guesses can be fragments themselves.
    """
    q, t = normalize(query), normalize(title)
    return fuzz.token_set_ratio(q, t) >= 85 and len(t) >= 0.6 * len(q)


def _title_search(http: apis.Http, s: Settings, pdf: PdfContent, title: str,
                  surname: Optional[str] = None) -> Tuple[Optional[dict], str]:
    """CrossRef, then OpenAlex: the best hit that resembles `title` and validates.

    OpenAlex is asked whenever CrossRef has nothing *usable*, not only nothing at all:
    CrossRef's top rows can all be records *about* the paper ("Faculty Opinions
    recommendation of <title>") that rightly fail validation.
    """
    searches = (("crossref-search", lambda: apis.crossref_search(http, title, surname)),
                ("openalex-search", lambda: apis.openalex_search(http, title, s.openalex_api_key)))
    for via, search in searches:
        scored = [(validate(h, pdf, all_authors=True), h) for h in search() if _resembles(title, h.get("title"))]
        if scored:
            (conf, _), h = max(scored, key=lambda x: (_RANK[x[0][0]], x[0][1]))
            if _good(conf):
                h["_via"] = via
                return h, conf
    return None, "low"


def _lookup(http: apis.Http, s: Settings, pdf: PdfContent, doi: Optional[str], title: Optional[str],
            surname: Optional[str] = None) -> Tuple[Optional[dict], str]:
    """A proposed DOI if it validates, else a search for the proposed title."""
    doi = clean_doi(doi) if doi else None
    if doi and (f := _resolve(http, s, "doi", doi)) and _good(conf := validate(f, pdf)[0]):
        return f, conf
    return _title_search(http, s, pdf, title, surname) if title else (None, "low")


def _grobid(s: Settings, path: Path) -> Optional[dict]:
    """Header-only GROBID call → {'title', 'doi'}; zero tokens."""
    try:
        with open(path, "rb") as fh:
            r = requests.post(f"{s.grobid_url.rstrip('/')}/api/processHeaderDocument",
                              files={"input": fh}, data={"consolidateHeader": "0"},
                              headers={"Accept": "application/x-bibtex"}, timeout=120)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    bib = lambda k: (m := re.search(rf"\b{k}\s*=\s*\{{(.*?)\}}\s*,?\s*$", r.text, re.M | re.I)) and m[1]
    return {"title": bib("title"), "doi": bib("doi")}


def _best_candidate(http: apis.Http, s: Settings, pdf: PdfContent) -> Optional[Hit]:
    """Tiers 1–2: the best confirmed identifier embedded in or printed on the PDF."""
    best = None
    for c in find_candidates(pdf)[:6]:
        if not (f := _resolve(http, s, c.kind, c.value)):
            continue
        conf = validate(f, pdf)[0]
        if _good(conf) and (best is None or _RANK[conf] > _RANK[best[1]]):
            best = (f, conf, f"{c.kind}:{c.source}")
        if conf == "high":
            break
    return best


def _fallbacks(http: apis.Http, s: Settings, pdf: PdfContent) -> Iterator[Hit]:
    """Tiers 3–5, cheapest first; generated lazily so later tiers run only when needed."""
    queries = (*pdf.title_guesses, pdf.info.get("title"), pdf.opening)
    for title in dict.fromkeys(t for t in queries if t and len(t) >= 15):
        yield (*_title_search(http, s, pdf, title), "title-search")
    if s.grobid_url and (g := _grobid(s, pdf.path)):
        yield (*_lookup(http, s, pdf, g["doi"], g["title"]), "grobid")
    if s.llm and pdf.page_texts and (key := s.llm.identify(pdf.page_texts[0])):
        yield (*_lookup(http, s, pdf, key.get("doi"), key["title"], key.get("first_author_surname")), "llm-key")


def _merge(rec: Record, f: dict, *, override: bool = False) -> None:
    """Copy API fields onto the record; without `override`, only fill gaps."""
    for k, v in f.items():
        if k.startswith("_") or k == "oa" or v in (None, "", []) or not hasattr(rec, k):
            continue
        if override or getattr(rec, k) in (None, "", [], False):
            setattr(rec, k, v)


def _apply(rec: Record, f: dict, conf: str, how: str) -> None:
    _merge(rec, f, override=True)
    rec.confidence = conf
    rec.resolved_via = [how, f["_via"]]
    if f.get("abstract"):
        rec.abstract_source = f["_via"]
    if f.get("oa"):
        rec.oa = f["oa"]
    rec.oa.license = rec.oa.license or f.get("_license")


def identify(http: apis.Http, s: Settings, pdf: PdfContent, rec: Record) -> None:
    hit = _best_candidate(http, s, pdf) or next((h for h in _fallbacks(http, s, pdf) if _good(h[1])), None)
    if hit:
        _apply(rec, *hit)
        return
    rec.resolved_via = ["pdf-only"]
    rec.title = pdf.title_guess or pdf.info.get("title") or pdf.path.stem
    if rec.title.isupper():                    # a title page in capitals; don't cite it shouting
        rec.title = rec.title.capitalize()
    if pdf.info.get("author"):
        rec.authors = [Author.from_display(a) for a in
                       re.split(r";|\band\b|,(?=\s*[A-Z][a-z]+\s+[A-Z])", pdf.info["author"]) if a.strip()]
    rec.notes.append("No record could be confirmed by title and authors; only PDF-derived fields are present.")
    if pdf.is_probably_scanned:
        rec.notes.append("PDF has no usable text layer — install Tesseract for automatic OCR "
                         "of page 1, or run ocrmypdf on the file, and retry with --force.")


# ---------------------------------------------------------------------------
# version policy and enrichment
# ---------------------------------------------------------------------------

def apply_version_policy(http: apis.Http, s: Settings, rec: Record) -> None:
    other = rec.related_doi
    if not other:
        return
    if rec.related_relation == "has-preprint":
        rec.notes.append(f"Preprint version: https://doi.org/{other}")
        return
    pub = apis.crossref_work(http, other) if s.prefer == "published" else None
    if not pub:
        rec.notes.append(f"Published version exists: https://doi.org/{other}")
        return
    held = rec.doi
    for k in ("volume", "issue", "first_page", "last_page", "article_number", "issn", "journal_abbrev"):
        setattr(rec, k, None)
    _merge(rec, pub, override=True)
    rec.is_preprint = False
    rec.related_doi, rec.related_relation = held, "has-preprint"
    rec.oa = OpenAccess()                      # the preprint's OA no longer applies; enrich() refetches
    rec.resolved_via.append("published-version")
    rec.notes.append(f"PDF is the preprint ({held}); citing the published version.")


def enrich(http: apis.Http, s: Settings, pdf: PdfContent, rec: Record) -> None:
    # Abstract priority: CrossRef > Europe PMC (curated) > OpenAlex (rebuilt from an
    # inverted index, occasionally truncated) > PDF text. Papers without a DOI (often
    # PubMed-indexed papers from before ~2004) are enriched by PMID.
    oa_abstract = None
    if rec.doi and "openalex" not in rec.resolved_via \
            and (w := apis.openalex_work(http, rec.doi, s.openalex_api_key)):
        oa_abstract = w.pop("abstract", None)
        _merge(rec, w)
        if w.get("oa") and not rec.oa.source:
            rec.oa = w["oa"]
        rec.resolved_via.append("openalex")
    if (rec.doi or rec.pmid) and (not rec.abstract or not rec.pmid or not rec.journal) \
            and (ep := apis.europepmc(http, rec.doi, rec.pmid)):
        if ep["abstract"] and not rec.abstract:
            rec.abstract_source = "europepmc"
        if ep["issn"] and not rec.journal:
            rec.journal = apis.journal_name(http, ep["issn"])     # else NLM's "Cancer research"
        _merge(rec, ep)
        rec.resolved_via.append("europepmc")
    if oa_abstract and not rec.abstract:
        rec.abstract, rec.abstract_source = oa_abstract, "openalex"
    if rec.doi and s.unpaywall_email and (up := apis.unpaywall(http, rec.doi, s.unpaywall_email)) and up.status:
        up.license = up.license or rec.oa.license
        rec.oa = up
    if not rec.abstract and (a := abstract_from_text(pdf.first_pages)):
        rec.abstract, rec.abstract_source = a, "pdf-text"

    # Preprint servers are green OA by definition.
    preprint_doi = bool(rec.doi and rec.doi.startswith(PREPRINT_DOI_PREFIXES))
    if preprint_doi:
        rec.is_preprint, rec.item_type = True, "preprint"
    if not rec.oa.status and (preprint_doi or rec.arxiv_id):
        rec.oa = OpenAccess(is_oa=True, status="green", version="submittedVersion", source="rule",
                            url=rec.url if preprint_doi else f"https://arxiv.org/abs/{rec.arxiv_id}")
    if rec.doi and not rec.oa.status:
        rec.notes.append("No OA information found for this DOI.")


# ---------------------------------------------------------------------------

def process_pdf(path: Path, http: apis.Http, s: Settings) -> Record:
    rec = Record(pdf_path=str(path.resolve()))
    try:
        pdf = read_pdf(path)
    except Exception as e:                     # noqa: BLE001 — an unreadable PDF is a result, not a crash
        rec.title = path.stem
        rec.notes.append(f"Could not open PDF: {e}")
        return rec
    rec.pdf_sha256 = pdf.sha256
    if pdf.ocr:
        rec.notes.append("No usable text layer: page 1 was read by OCR.")
    identify(http, s, pdf, rec)
    apply_version_policy(http, s, rec)
    enrich(http, s, pdf, rec)
    rec.links = find_links(pdf, s.link_checker)
    return rec
