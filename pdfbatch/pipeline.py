"""Per-PDF pipeline.

  PDF ──► identifier tiers (0 tokens) ──► resolver chain ──► validation
     │        1 XMP / Info dict              CrossRef or arXiv
     │        2 regex: header/margins,       + OpenAlex  (fields + OA)
     │          page 1, page 2, last page    + Europe PMC (abstract, PMID)
     │        3 largest-font title → CrossRef/OpenAlex search
     │        4 GROBID header (optional)
     │        5 LLM search key (optional, ~1.5k tokens)
     └──► OA (Unpaywall ▸ OpenAlex ▸ preprint-server rule)
     └──► code & data links from the full text (repo's patterns + labeler)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import requests
from rapidfuzz import fuzz

from . import apis
from .identifiers import REFS_HEADING_RE, clean_doi, find_candidates
from .llm import LlmIdentifier
from .pdf_reader import PdfContent, abstract_from_text, read_pdf
from .record import Author, Link, OpenAccess, Record

from .link_labeler import HeuristicLabeler
from .link_patterns import BLOCKLIST_URLS as _BLOCKLIST, extract_from_text

PREPRINT_DOI_PREFIXES = ("10.1101/", "10.48550/", "10.21203/", "10.20944/", "10.31234/",
                         "10.31219/", "10.36227/", "10.22541/", "10.2139/ssrn")


@dataclass
class Settings:
    mailto: str
    unpaywall_email: Optional[str] = None
    openalex_api_key: Optional[str] = None
    prefer: str = "held"                 # 'held' | 'published'
    grobid_url: Optional[str] = None
    llm: Optional[LlmIdentifier] = None
    link_validator: Optional[object] = None   # pdfbatch.link_validator.LinkValidator
    max_links_per_kind: int = 5
    blocklist: List[str] = field(default_factory=lambda: list(_BLOCKLIST))


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("ﬁ", "fi").replace("ﬂ", "fl")
    s = re.sub(r"-\s*\n\s*", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def validate(title: Optional[str], authors: List[Author], pdf: PdfContent) -> Tuple[str, float, bool]:
    """Does the resolved record describe *this* PDF? → (confidence, title_score, author_hit)."""
    if not title:
        return "low", 0.0, False
    hay = _norm("\n".join(pdf.page_texts[:3]) + " " + str(pdf.info.get("title", "")))
    t = _norm(title)
    score = fuzz.partial_ratio(t, hay) if t and hay else 0.0
    fams = [_norm(a.family or a.literal.split(" ")[-1]) for a in authors[:5]]
    author_hit = any(f and len(f) > 1 and re.search(rf"\b{re.escape(f)}\b", hay) for f in fams)
    if score >= 90 and author_hit:
        return "high", score, author_hit
    if score >= 90 or (score >= 75 and author_hit):
        return "medium", score, author_hit
    return "low", score, author_hit


_RANK = {"high": 3, "medium": 2, "low": 1, "unresolved": 0}


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------

def _resolve_doi(http: apis.Http, s: Settings, doi: str) -> Optional[dict]:
    f = apis.crossref_work(http, doi)
    if f:
        f["_via"] = ["crossref"]
        return f
    f = apis.openalex_work(http, doi, s.openalex_api_key)   # DataCite DOIs (arXiv, Zenodo, …)
    if f:
        f["_via"] = ["openalex"]
    return f


def _resolve_arxiv(http: apis.Http, arxiv_id: str) -> Optional[dict]:
    f = apis.arxiv_work(http, arxiv_id)
    if f:
        f["_via"] = ["arxiv"]
    return f


def _title_search(http: apis.Http, s: Settings, title: str, pdf: PdfContent,
                  surname: Optional[str] = None) -> Tuple[Optional[dict], str]:
    best, best_conf, best_score = None, "low", 0.0
    hits = [(h, "crossref-search") for h in apis.crossref_search(http, title, surname)]
    if not hits:
        hits = [(h, "openalex-search") for h in apis.openalex_search(http, title, s.openalex_api_key)]
    for h, via in hits:
        sim = fuzz.token_set_ratio(_norm(title), _norm(h.get("title") or ""))
        if sim < 85:
            continue
        conf, score, _ = validate(h.get("title"), h.get("authors") or [], pdf)
        if (_RANK[conf], score) > (_RANK[best_conf], best_score):
            best, best_conf, best_score = h, conf, score
            h["_via"] = [via]
    return best, best_conf


def _grobid(s: Settings, path: Path) -> Optional[dict]:
    """Header-only GROBID call → {'title', 'doi'}; zero tokens."""
    if not s.grobid_url:
        return None
    try:
        with open(path, "rb") as fh:
            r = requests.post(f"{s.grobid_url.rstrip('/')}/api/processHeaderDocument",
                              files={"input": fh}, data={"consolidateHeader": "0"},
                              headers={"Accept": "application/x-bibtex"}, timeout=120)
        if r.status_code != 200:
            return None
    except requests.RequestException:
        return None
    get = lambda k: (re.search(rf"\b{k}\s*=\s*\{{(.*?)\}}\s*,?\s*$", r.text, re.M | re.I) or [None, None])[1]
    return {"title": get("title"), "doi": get("doi")}


def _merge(rec: Record, fields: Optional[dict], *, override: bool = False) -> None:
    """First-hit-wins per field unless override."""
    if not fields:
        return
    for k, v in fields.items():
        if k.startswith("_") or k == "oa" or v in (None, "", []):
            continue
        if not hasattr(rec, k):
            continue
        cur = getattr(rec, k)
        if override or cur in (None, "", [], False) or (k == "item_type" and cur == "journal-article"):
            setattr(rec, k, v)


def _apply(rec: Record, fields: dict, conf: str, how: str) -> None:
    _merge(rec, fields, override=True)
    rec.confidence = conf
    rec.resolved_via = [how] + fields.get("_via", [])
    if fields.get("abstract"):
        rec.abstract_source = fields["_via"][0]
    if fields.get("oa"):
        rec.oa = fields["oa"]
    if fields.get("_crossref_license"):
        rec.oa.license = rec.oa.license or fields["_crossref_license"]


def identify(http: apis.Http, s: Settings, pdf: PdfContent, rec: Record) -> None:
    # Tiers 1–2: identifiers printed in / embedded in the PDF.
    best: Optional[Tuple[dict, str, str]] = None
    for c in find_candidates(pdf)[:6]:
        f = _resolve_doi(http, s, c.value) if c.kind == "doi" else _resolve_arxiv(http, c.value)
        if not f:
            continue
        conf, score, _ = validate(f.get("title"), f.get("authors") or [], pdf)
        if best is None or _RANK[conf] > _RANK[best[1]]:
            best = (f, conf, f"{c.kind}:{c.source}")
        if conf == "high":
            break
    if best and _RANK[best[1]] >= 2:
        _apply(rec, *best)
        return

    # Tier 3: title from the PDF (largest font, then the Info dict) → search.
    titles = [t for t in (pdf.title_guess, pdf.info.get("title")) if t and len(t) >= 15]
    for t in dict.fromkeys(titles):
        f, conf = _title_search(http, s, t, pdf)
        if f and _RANK[conf] >= 2:
            _apply(rec, f, conf, "title-search")
            return

    # Tier 4: GROBID header extraction.
    g = _grobid(s, pdf.path)
    if g:
        d = clean_doi(g["doi"]) if g.get("doi") else None
        f = _resolve_doi(http, s, d) if d else None
        conf = validate(f.get("title"), f.get("authors") or [], pdf)[0] if f else "low"
        if f and _RANK[conf] >= 2:
            _apply(rec, f, conf, "grobid-doi"); return
        if g.get("title"):
            f, conf = _title_search(http, s, g["title"], pdf)
            if f and _RANK[conf] >= 2:
                _apply(rec, f, conf, "grobid-title"); return

    # Tier 5: LLM search key.
    if s.llm and pdf.page_texts:
        key = s.llm.identify(pdf.page_texts[0])
        if key:
            d = clean_doi(key["doi"]) if key.get("doi") else None
            f = _resolve_doi(http, s, d) if d else None
            conf = validate(f.get("title"), f.get("authors") or [], pdf)[0] if f else "low"
            if not (f and _RANK[conf] >= 2):
                f, conf = _title_search(http, s, key["title"], pdf, key.get("first_author_surname"))
            if f and _RANK[conf] >= 2:
                _apply(rec, f, conf, "llm-key"); return

    # Nothing validated. Keep a low-confidence API hit if we had one, flagged.
    if best:
        _apply(rec, *best)
        rec.notes.append(f"Resolved record did not match the PDF text well ({best[2]}); check it.")
        return
    rec.confidence = "unresolved"
    rec.resolved_via = ["pdf-only"]
    rec.title = pdf.title_guess or pdf.info.get("title") or pdf.path.stem
    if pdf.info.get("author"):
        rec.authors = [Author.from_display(a) for a in re.split(r";|\band\b|,(?=\s*[A-Z][a-z]+\s+[A-Z])", pdf.info["author"]) if a.strip()]
    rec.notes.append("No identifier could be resolved; only PDF-derived fields are present.")
    if pdf.is_probably_scanned:
        rec.notes.append("PDF has (almost) no text layer — run OCR (e.g. ocrmypdf) and retry.")


# ---------------------------------------------------------------------------
# enrichment
# ---------------------------------------------------------------------------

def enrich(http: apis.Http, s: Settings, pdf: PdfContent, rec: Record) -> None:
    if rec.doi:
        # Abstract priority: CrossRef > Europe PMC (curated) > OpenAlex (rebuilt
        # from an inverted index, occasionally truncated) > PDF text.
        oa_abstract = None
        if "openalex" not in rec.resolved_via:
            oa_fields = apis.openalex_work(http, rec.doi, s.openalex_api_key)
            if oa_fields:
                oa_abstract = oa_fields.pop("abstract", None)
                _merge(rec, oa_fields)
                if oa_fields.get("oa") and not rec.oa.source:
                    rec.oa = oa_fields["oa"]
                rec.resolved_via.append("openalex")
        if not rec.abstract or not rec.pmid:
            ep = apis.europepmc(http, rec.doi)
            if ep:
                had_abstract = bool(rec.abstract)
                _merge(rec, ep)
                if rec.abstract and not had_abstract:
                    rec.abstract_source = "europepmc"
                rec.resolved_via.append("europepmc")
        if not rec.abstract and oa_abstract:
            rec.abstract, rec.abstract_source = oa_abstract, "openalex"
        if s.unpaywall_email:
            up = apis.unpaywall(http, rec.doi, s.unpaywall_email)
            if up and up.status:
                lic = up.license or rec.oa.license
                rec.oa = up
                rec.oa.license = lic

    if not rec.abstract:
        a = abstract_from_text(pdf.first_pages)
        if a:
            rec.abstract, rec.abstract_source = a, "pdf-text"

    # Preprint servers are green OA by definition.
    if rec.doi and rec.doi.startswith(PREPRINT_DOI_PREFIXES):
        rec.is_preprint = True
        rec.item_type = "preprint"
        if not rec.oa.status:
            rec.oa = OpenAccess(is_oa=True, status="green", version="submittedVersion",
                                url=rec.url, source="rule")
    if rec.arxiv_id and not rec.oa.status:
        rec.oa = OpenAccess(is_oa=True, status="green", version="submittedVersion",
                            url=f"https://arxiv.org/abs/{rec.arxiv_id}", source="rule")
    if rec.doi and not rec.oa.status:
        rec.notes.append("No OA information found for this DOI.")

    if rec.date and not rec.year:
        rec.year = rec.date[:4]


def apply_version_policy(http: apis.Http, s: Settings, rec: Record) -> None:
    if not rec.related_doi:
        return
    if rec.related_relation == "is-preprint-of":
        if s.prefer == "published":
            pub = apis.crossref_work(http, rec.related_doi)
            if pub:
                held = rec.doi
                keep_abstract = rec.abstract
                for k in ("volume", "issue", "first_page", "last_page", "article_number", "issn",
                          "journal_abbrev"):
                    setattr(rec, k, None)
                _merge(rec, pub, override=True)
                rec.abstract = rec.abstract or keep_abstract
                rec.is_preprint = False
                rec.related_doi, rec.related_relation = held, "has-preprint"
                rec.resolved_via.append("published-version")
                rec.notes.append(f"PDF is the preprint ({held}); citing the published version.")
                oa = apis.openalex_work(http, rec.doi, s.openalex_api_key)
                if oa and oa.get("oa"):
                    rec.oa = oa["oa"]
                if s.unpaywall_email:
                    up = apis.unpaywall(http, rec.doi, s.unpaywall_email)
                    if up and up.status:
                        rec.oa = up
                return
        rec.notes.append(f"Published version exists: https://doi.org/{rec.related_doi}")
    elif rec.related_relation == "has-preprint":
        rec.notes.append(f"Preprint version: https://doi.org/{rec.related_doi}")


# ---------------------------------------------------------------------------
# code & data links (repo's patterns + heuristic labeler, on the local full text)
# ---------------------------------------------------------------------------

_AFTER_REFS = re.compile(
    r"^\s*(?:\d+\.?\s*)?(methods|online methods|materials and methods|data availability|"
    r"code availability|data and code availability|resource availability|acknowledg(?:e)?ments?|"
    r"author contributions|supplementary|appendix|competing interests|extended data|"
    r"key resources table|star\s*methods)\b",
    re.I | re.M,
)


def _reference_spans(text: str) -> List[Tuple[int, int]]:
    spans = []
    for m in REFS_HEADING_RE.finditer(text):
        nxt = _AFTER_REFS.search(text, m.end())
        spans.append((m.start(), nxt.start() if nxt else len(text)))
    return spans


def find_links(s: Settings, pdf: PdfContent) -> List[Link]:
    text = pdf.full_text
    # Re-join URLs broken at line ends ("https://github.com/org/\nrepo").
    text = re.sub(r"(https?://[^\s]*[/\-_.])\n(?=[A-Za-z0-9])", r"\1", text)
    annot = "\n".join(u for _, u in pdf.link_uris)
    spans = _reference_spans(text)
    in_refs = lambda i: any(a <= i < b for a, b in spans)

    found, seen = [], set()
    for cl in extract_from_text(text) + extract_from_text(annot):
        if cl.url in seen:
            continue
        seen.add(cl.url)
        pos = text.find(cl.original_url)
        if pos < 0:
            pos = text.find(cl.url)
        if pos < 0:
            pos = text.find(cl.original_url[:40])
        if pos >= 0 and in_refs(pos):
            continue                       # cited tool, not the paper's own code/data
        if cl.url.rstrip("/").lower() in {b.rstrip("/").lower() for b in s.blocklist}:
            continue
        if s.link_validator is not None and not s.link_validator.is_alive(cl):
            continue
        found.append(cl)

    labels = HeuristicLabeler().label(text, found)
    out, counts = [], {"code": 0, "data": 0}
    for cl in found:
        if counts[cl.kind] >= s.max_links_per_kind:
            continue
        counts[cl.kind] += 1
        label = labels.get(cl.url)
        # The repo labeler takes the first trigger in its window, not the nearest,
        # so "Code availability" can bleed onto a data link. Generic labels add
        # nothing beyond `kind` anyway, so keep only informative ones.
        if label and label.lower() in {"code", "data", "repository", "github", "gitlab", "zenodo"}:
            label = None
        out.append(Link(kind=cl.kind, platform=cl.platform, url=cl.url, label=label))
    return out


# ---------------------------------------------------------------------------

def process_pdf(path: Path, http: apis.Http, s: Settings) -> Record:
    rec = Record(pdf_path=str(path.resolve()))
    try:
        pdf = read_pdf(path)
    except Exception as e:                              # noqa: BLE001
        rec.title = path.stem
        rec.notes.append(f"Could not open PDF: {e}")
        return rec
    rec.pdf_sha256 = pdf.sha256
    identify(http, s, pdf, rec)
    apply_version_policy(http, s, rec)
    enrich(http, s, pdf, rec)
    rec.links = find_links(s, pdf)
    return rec
