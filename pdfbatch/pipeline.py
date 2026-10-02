"""Per-PDF pipeline.

  PDF ──► identifier tiers (0 tokens) ──► resolver chain ──► validation
     │        1 XMP / Info dict              CrossRef or arXiv
     │        2 regex: header/margins,       + OpenAlex  (fields + OA)
     │          page 1, page 2, last page    + Europe PMC (abstract, PMID)
     │        3 largest-font title → CrossRef/OpenAlex search
     │        4 GROBID header (optional)
     │        5 LLM search key (optional, ~1.5k tokens)
     └──► OA (Unpaywall ▸ OpenAlex ▸ preprint-server rule)
     └──► code & data links from the full text
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Iterator, Optional, Tuple

import requests
from rapidfuzz import fuzz

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


def validate(f: dict, pdf: PdfContent) -> Tuple[str, float]:
    """Does the API record describe *this* PDF? → (confidence, title score)."""
    title = normalize(f.get("title"))
    if not title:
        return "low", 0.0
    score = fuzz.partial_ratio(title, pdf.head) if pdf.head else 0.0
    surnames = (normalize(a.family or a.literal.split(" ")[-1]) for a in (f.get("authors") or [])[:5])
    named = any(len(s) > 1 and re.search(rf"\b{re.escape(s)}\b", pdf.head) for s in surnames)
    if score >= 90 and named:
        return "high", score
    if score >= 90 or (score >= 75 and named):
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


def _title_search(http: apis.Http, s: Settings, pdf: PdfContent, title: str,
                  surname: Optional[str] = None) -> Tuple[Optional[dict], str]:
    hits = ([(h, "crossref-search") for h in apis.crossref_search(http, title, surname)]
            or [(h, "openalex-search") for h in apis.openalex_search(http, title, s.openalex_api_key)])
    scored = [(validate(h, pdf), h, via) for h, via in hits
              if fuzz.token_set_ratio(normalize(title), normalize(h.get("title"))) >= 85]
    if not scored:
        return None, "low"
    (conf, _), h, via = max(scored, key=lambda x: (_RANK[x[0][0]], x[0][1]))
    h["_via"] = via
    return h, conf


def _lookup(http: apis.Http, s: Settings, pdf: PdfContent, doi: Optional[str], title: Optional[str],
            surname: Optional[str] = None) -> Tuple[Optional[dict], str, str]:
    """A DOI if it validates, else a title search. → (fields, confidence, 'doi' | 'title')."""
    doi = clean_doi(doi) if doi else None
    f = _resolve(http, s, "doi", doi) if doi else None
    conf = validate(f, pdf)[0] if f else "low"
    if _good(conf):
        return f, conf, "doi"
    if not title:
        return None, "low", "title"
    return (*_title_search(http, s, pdf, title, surname), "title")


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
    """Tiers 1–2: the best-validating identifier embedded in or printed on the PDF."""
    best = None
    for c in find_candidates(pdf)[:6]:
        f = _resolve(http, s, c.kind, c.value)
        if not f:
            continue
        conf = validate(f, pdf)[0]
        if best is None or _RANK[conf] > _RANK[best[1]]:
            best = (f, conf, f"{c.kind}:{c.source}")
        if conf == "high":
            break
    return best


def _fallbacks(http: apis.Http, s: Settings, pdf: PdfContent) -> Iterator[Hit]:
    """Tiers 3–5, cheapest first; generated lazily so later tiers run only when needed."""
    for title in dict.fromkeys(t for t in (pdf.title_guess, pdf.info.get("title")) if t and len(t) >= 15):
        yield (*_title_search(http, s, pdf, title), "title-search")
    if s.grobid_url and (g := _grobid(s, pdf.path)):
        f, conf, by = _lookup(http, s, pdf, g["doi"], g["title"])
        yield f, conf, f"grobid-{by}"
    if s.llm and pdf.page_texts and (key := s.llm.identify(pdf.page_texts[0])):
        f, conf, _ = _lookup(http, s, pdf, key.get("doi"), key["title"], key.get("first_author_surname"))
        yield f, conf, "llm-key"


def _merge(rec: Record, f: dict, *, override: bool = False) -> None:
    """Copy API fields onto the record; without `override`, only fill gaps."""
    for k, v in f.items():
        if k.startswith("_") or k == "oa" or v in (None, "", []) or not hasattr(rec, k):
            continue
        cur = getattr(rec, k)
        if override or cur in (None, "", [], False) or (k == "item_type" and cur == "journal-article"):
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
    best = _best_candidate(http, s, pdf)
    tiers = chain([best] if best else [], _fallbacks(http, s, pdf))
    hit = next((h for h in tiers if h[0] and _good(h[1])), best)
    if hit:
        _apply(rec, *hit)
        if not _good(hit[1]):
            rec.notes.append(f"Resolved record did not match the PDF text well ({hit[2]}); check it.")
        return
    rec.confidence, rec.resolved_via = "unresolved", ["pdf-only"]
    rec.title = pdf.title_guess or pdf.info.get("title") or pdf.path.stem
    if pdf.info.get("author"):
        rec.authors = [Author.from_display(a) for a in
                       re.split(r";|\band\b|,(?=\s*[A-Z][a-z]+\s+[A-Z])", pdf.info["author"]) if a.strip()]
    rec.notes.append("No identifier could be resolved; only PDF-derived fields are present.")
    if pdf.is_probably_scanned:
        rec.notes.append("PDF has (almost) no text layer — run OCR (e.g. ocrmypdf) and retry.")


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
    # inverted index, occasionally truncated) > PDF text.
    if rec.doi:
        oa_abstract = None
        if "openalex" not in rec.resolved_via and (w := apis.openalex_work(http, rec.doi, s.openalex_api_key)):
            oa_abstract = w.pop("abstract", None)
            _merge(rec, w)
            if w.get("oa") and not rec.oa.source:
                rec.oa = w["oa"]
            rec.resolved_via.append("openalex")
        if (not rec.abstract or not rec.pmid) and (ep := apis.europepmc(http, rec.doi)):
            if ep["abstract"] and not rec.abstract:
                rec.abstract_source = "europepmc"
            _merge(rec, ep)
            rec.resolved_via.append("europepmc")
        if oa_abstract and not rec.abstract:
            rec.abstract, rec.abstract_source = oa_abstract, "openalex"
        if s.unpaywall_email and (up := apis.unpaywall(http, rec.doi, s.unpaywall_email)) and up.status:
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
    identify(http, s, pdf, rec)
    apply_version_policy(http, s, rec)
    enrich(http, s, pdf, rec)
    rec.links = find_links(pdf, s.link_checker)
    return rec
