"""Thin, cached clients for the structured APIs.

They return the raw fields a reference manager needs (given/family names,
volume, issue, pages, ISSN, item type, relations, OA locations).

Every function returns a dict whose keys are `Record` field names (plus
'authors' as a list of `Author` and 'oa' as an `OpenAccess`) or None.
"""

from __future__ import annotations

import html
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

import diskcache
import requests

from .record import Author, OpenAccess

_RETRY = {429, 500, 502, 503, 504}


class Http:
    """requests + diskcache + polite headers + backoff. One instance per run."""

    def __init__(self, mailto: str, cache_dir: Optional[Path], ttl_days: int = 30, timeout: int = 20):
        self.mailto = mailto
        self.timeout = timeout
        self.ttl = ttl_days * 86400
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"pdfbatch/1.0 (mailto:{mailto})"
        self.cache = diskcache.Cache(str(cache_dir)) if cache_dir else None
        self._last: Dict[str, float] = {}

    def _throttle(self, host: str, min_interval: float) -> None:
        wait = self._last.get(host, 0) + min_interval - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.time()

    def get(self, url: str, params: Optional[dict] = None, *, as_json: bool = True,
            min_interval: float = 0.12, retries: int = 3) -> Any:
        key = url + "?" + urllib.parse.urlencode(sorted((params or {}).items()))
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return hit if hit != "__404__" else None
        host = urllib.parse.urlparse(url).netloc
        delay = 1.0
        for attempt in range(retries + 1):
            self._throttle(host, min_interval)
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException:
                if attempt == retries:
                    return None
                time.sleep(delay); delay *= 2
                continue
            if r.status_code == 404:
                if self.cache is not None:
                    self.cache.set(key, "__404__", expire=self.ttl)
                return None
            if r.status_code in _RETRY and attempt < retries:
                ra = r.headers.get("Retry-After")
                time.sleep(float(ra) if ra and ra.isdigit() else delay); delay *= 2
                continue
            if r.status_code != 200:
                return None
            try:
                body = r.json() if as_json else r.text
            except ValueError:
                return None
            if self.cache is not None:
                self.cache.set(key, body, expire=self.ttl)
            return body
        return None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def strip_markup(s: Optional[str]) -> Optional[str]:
    """JATS / HTML → plain text."""
    if not s:
        return None
    s = re.sub(r"<jats:title>\s*(abstract|summary)\s*</jats:title>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"^\s*(abstract|summary)[\s:.]+", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _date_from_parts(obj: Optional[dict]) -> Optional[str]:
    try:
        parts = obj["date-parts"][0]
    except (TypeError, KeyError, IndexError):
        return None
    parts = [p for p in parts if p]
    if not parts:
        return None
    return "-".join(f"{int(p):02d}" if i else str(p) for i, p in enumerate(parts))


def _inverted_to_text(idx: Optional[dict]) -> Optional[str]:
    if not idx:
        return None
    pos = {}
    for w, ps in idx.items():
        for p in ps:
            pos[p] = w
    return " ".join(pos[i] for i in sorted(pos)) or None


def _norm_license(lic: Optional[str]) -> Optional[str]:
    if not lic:
        return None
    lic = lic.lower()
    m = re.search(r"creativecommons\.org/(?:licenses|publicdomain)/([a-z\-]+)", lic)
    if m:
        return "cc0" if m.group(1) == "zero" else f"cc-{m.group(1)}"
    return lic


# ---------------------------------------------------------------------------
# CrossRef
# ---------------------------------------------------------------------------

def crossref_work(http: Http, doi: str) -> Optional[dict]:
    body = http.get(f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}",
                    params={"mailto": http.mailto})
    if not body or "message" not in body:
        return None
    return crossref_to_fields(body["message"])


def crossref_to_fields(m: dict) -> dict:
    authors = []
    for a in m.get("author") or []:
        if a.get("family"):
            authors.append(Author(family=a["family"].strip(), given=(a.get("given") or "").strip()))
        elif a.get("name"):
            authors.append(Author(literal=a["name"].strip()))
    date = None
    for k in ("published-print", "published-online", "issued", "published", "posted", "created"):
        date = _date_from_parts(m.get(k))
        if date:
            break
    page = (m.get("page") or "").replace("–", "-")
    first, _, last = page.partition("-")
    ctype = m.get("type") or "journal-article"
    is_preprint = ctype == "posted-content" and (m.get("subtype") in (None, "preprint"))
    journal = (m.get("container-title") or [None])[0]
    if is_preprint and not journal:
        journal = (m.get("institution") or [{}])[0].get("name") or m.get("publisher")
    rel = m.get("relation") or {}
    related_doi = related_rel = None
    for r in ("is-preprint-of", "has-preprint"):
        for item in rel.get(r) or []:
            if item.get("id-type") == "doi":
                related_doi, related_rel = item["id"].lower(), r
                break
        if related_doi:
            break
    issn = None
    for t in m.get("issn-type") or []:
        if t.get("type") == "print":
            issn = t["value"]
    issn = issn or (m.get("ISSN") or [None])[0]
    lic = None
    for l in m.get("license") or []:
        if l.get("content-version") in ("vor", "unspecified"):
            lic = _norm_license(l.get("URL"))
    return {
        "item_type": "preprint" if is_preprint else ctype,
        "is_preprint": is_preprint,
        "title": strip_markup((m.get("title") or [None])[0]),
        "authors": authors,
        "journal": strip_markup(journal),
        "journal_abbrev": (m.get("short-container-title") or [None])[0],
        "publisher": m.get("publisher"),
        "date": date,
        "year": date[:4] if date else None,
        "volume": m.get("volume"),
        "issue": m.get("issue"),
        "first_page": first or None,
        "last_page": last or None,
        "article_number": m.get("article-number"),
        "issn": issn,
        "doi": (m.get("DOI") or "").lower() or None,
        "url": f"https://doi.org/{m['DOI']}" if m.get("DOI") else m.get("URL"),
        "abstract": strip_markup(m.get("abstract")),
        "keywords": m.get("subject") or [],
        "related_doi": related_doi,
        "related_relation": related_rel,
        "_license": lic,
    }


def crossref_search(http: Http, title: str, author: Optional[str] = None, rows: int = 5) -> List[dict]:
    q = title + (f" {author}" if author else "")
    body = http.get("https://api.crossref.org/works",
                    params={"query.bibliographic": q, "rows": rows, "mailto": http.mailto})
    try:
        return [crossref_to_fields(it) for it in body["message"]["items"]]
    except (TypeError, KeyError):
        return []


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------

_OA_SELECT = ("id,doi,title,display_name,publication_date,type,authorships,primary_location,"
              "biblio,open_access,best_oa_location,abstract_inverted_index,ids")


def _openalex_params(http: Http, api_key: Optional[str]) -> dict:
    p = {"mailto": http.mailto, "select": _OA_SELECT}
    if api_key:
        p["api_key"] = api_key
    return p


def openalex_work(http: Http, doi: str, api_key: Optional[str] = None) -> Optional[dict]:
    body = http.get(f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi, safe='/')}",
                    params=_openalex_params(http, api_key))
    return openalex_to_fields(body) if body else None


def openalex_to_fields(w: dict) -> dict:
    authors = [Author.from_display((a.get("author") or {}).get("display_name") or "")
               for a in w.get("authorships") or []]
    src = ((w.get("primary_location") or {}).get("source") or {})
    bib = w.get("biblio") or {}
    oa = w.get("open_access") or {}
    best = w.get("best_oa_location") or {}
    ids = w.get("ids") or {}
    pmid = (ids.get("pmid") or "").rsplit("/", 1)[-1] or None
    pmcid = (ids.get("pmcid") or "").rsplit("/", 1)[-1] or None
    date = w.get("publication_date")
    first, last = bib.get("first_page"), bib.get("last_page")
    # OpenAlex puts article numbers in the page fields: first = last = "Article5".
    article = re.fullmatch(r"article\s*(?:no\.?\s*)?(\S+)", first or "", re.I)
    return {
        "title": w.get("title") or w.get("display_name"),
        "authors": [a for a in authors if a.family or a.literal],
        "journal": src.get("display_name"),
        "issn": src.get("issn_l"),
        "date": date,
        "year": date[:4] if date else None,
        "volume": bib.get("volume"),
        "issue": bib.get("issue"),
        "first_page": None if article else first,
        "last_page": None if article or last == first else last,
        "article_number": article and article[1],
        "doi": (w.get("doi") or "").replace("https://doi.org/", "").lower() or None,
        "abstract": _inverted_to_text(w.get("abstract_inverted_index")),
        "pmid": pmid,
        "pmcid": pmcid,
        "is_preprint": w.get("type") == "preprint",
        "oa": OpenAccess(
            is_oa=oa.get("is_oa"),
            status=oa.get("oa_status"),
            license=_norm_license(best.get("license")),
            version=best.get("version"),
            url=oa.get("oa_url") or best.get("pdf_url") or best.get("landing_page_url"),
            source="openalex",
        ) if oa else None,
    }


def openalex_search(http: Http, title: str, api_key: Optional[str] = None, rows: int = 5) -> List[dict]:
    p = _openalex_params(http, api_key)
    p.update({"search": title, "per-page": rows})
    body = http.get("https://api.openalex.org/works", params=p)
    try:
        return [openalex_to_fields(w) for w in body["results"]]
    except (TypeError, KeyError):
        return []


# ---------------------------------------------------------------------------
# Unpaywall (optional; needs a real e-mail address)
# ---------------------------------------------------------------------------

def unpaywall(http: Http, doi: str, email: str) -> Optional[OpenAccess]:
    body = http.get(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='/')}",
                    params={"email": email})
    if not body:
        return None
    best = body.get("best_oa_location") or {}
    return OpenAccess(
        is_oa=body.get("is_oa"),
        status=body.get("oa_status"),
        license=_norm_license(best.get("license")),
        version=best.get("version"),
        url=best.get("url_for_pdf") or best.get("url"),
        source="unpaywall",
    )


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------

_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def arxiv_work(http: Http, arxiv_id: str) -> Optional[dict]:
    xml = http.get("https://export.arxiv.org/api/query",
                   params={"id_list": arxiv_id, "max_results": 1}, as_json=False, min_interval=3.0)
    if not xml:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    e = root.find("a:entry", _ATOM)
    if e is None or e.find("a:title", _ATOM) is None:
        return None
    txt = lambda path: (e.findtext(path, default="", namespaces=_ATOM) or "").strip()
    date = txt("a:published")[:10] or None
    pub_doi = txt("arxiv:doi").lower() or None
    return {
        "item_type": "preprint",
        "is_preprint": True,
        "title": re.sub(r"\s+", " ", txt("a:title")),
        "authors": [Author.from_display(a.findtext("a:name", default="", namespaces=_ATOM))
                    for a in e.findall("a:author", _ATOM)],
        "journal": "arXiv",
        "publisher": "arXiv",
        "date": date,
        "year": date[:4] if date else None,
        "arxiv_id": arxiv_id,
        "doi": f"10.48550/arxiv.{arxiv_id}".lower() if re.match(r"^\d{4}\.", arxiv_id) else None,
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "abstract": re.sub(r"\s+", " ", txt("a:summary")) or None,
        "related_doi": pub_doi,
        "related_relation": "is-preprint-of" if pub_doi else None,
        "oa": OpenAccess(is_oa=True, status="green", version="submittedVersion",
                         url=f"https://arxiv.org/abs/{arxiv_id}", source="rule"),
    }


# ---------------------------------------------------------------------------
# Europe PMC (abstract + PMID/PMCID backfill for biomedical papers)
# ---------------------------------------------------------------------------

def europepmc(http: Http, doi: str) -> Optional[dict]:
    body = http.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                    params={"query": f'DOI:"{doi}"', "resultType": "core", "format": "json", "pageSize": 1})
    try:
        r = body["resultList"]["result"][0]
    except (TypeError, KeyError, IndexError):
        return None
    return {
        "abstract": strip_markup(r.get("abstractText")),
        "pmid": r.get("pmid"),
        "pmcid": r.get("pmcid"),
        "keywords": (r.get("keywordList") or {}).get("keyword") or [],
    }
