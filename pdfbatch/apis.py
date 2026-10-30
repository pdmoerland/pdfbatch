"""Thin, cached clients for the structured APIs.

They return the raw fields a reference manager needs (given/family names,
volume, issue, pages, ISSN, item type, relations, OA locations).

Every function returns a dict whose keys are `Record` field names (plus
'authors' as a list of `Author` and 'oa' as an `OpenAccess`) or None.
"""

from __future__ import annotations

import html
import os
import re
import sys
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import diskcache
import requests

from . import __version__
from .record import Author, OpenAccess

_RETRY = {429, 500, 502, 503, 504}
CACHE_TTL = 30 * 86400                  # seconds: API responses and link checks
MAX_RETRY_AFTER = 60.0                 # seconds; a server's "Retry-After: 3600" must not stall a worker
_UNCACHED_PARAMS = {"api_key"}          # sent, but never written to the cache file


def default_cache_dir() -> Path:
    """One per-user cache, wherever pdfbatch is run from, and outside synced project folders."""
    if env := os.environ.get("PDFBATCH_CACHE_DIR"):
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "pdfbatch" / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "pdfbatch"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "pdfbatch"


def _retry_after(value: str | None, default: float) -> float:
    """Seconds to wait: the server's Retry-After (decimals too), capped; `default` for an
    HTTP-date or no header."""
    try:
        return max(0.0, min(float(value), MAX_RETRY_AFTER))      # never negative
    except (TypeError, ValueError):
        return default


class Http:
    """requests + diskcache + polite headers + backoff. One instance per run."""

    def __init__(self, mailto: str, cache_dir: Path | None, timeout: int = 20):
        self.mailto = mailto
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"pdfbatch/{__version__} (mailto:{mailto})"
        self.cache = diskcache.Cache(str(cache_dir)) if cache_dir else None
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _throttle(self, host: str, min_interval: float) -> None:
        """Wait for this host's next free slot.

        The slot is reserved under the lock and slept for outside it: worker threads
        each claim a distinct time, and a wait for one host does not hold up the others.
        """
        with self._lock:
            slot = max(time.monotonic(), self._last.get(host, 0) + min_interval)
            self._last[host] = slot
        wait = slot - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, params: dict | None = None, *, as_json: bool = True,
            min_interval: float = 0.12, retries: int = 3) -> Any:
        key = url + "?" + urllib.parse.urlencode(sorted((k, v) for k, v in (params or {}).items()
                                                        if k not in _UNCACHED_PARAMS))
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
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code == 404:
                if self.cache is not None:
                    self.cache.set(key, "__404__", expire=CACHE_TTL)
                return None
            if r.status_code in _RETRY and attempt < retries:
                time.sleep(_retry_after(r.headers.get("Retry-After"), delay))
                delay *= 2
                continue
            if r.status_code != 200:
                return None
            try:
                body = r.json() if as_json else r.text
            except ValueError:
                return None
            if self.cache is not None:
                self.cache.set(key, body, expire=CACHE_TTL)
            return body
        return None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def strip_markup(s: str | None) -> str | None:
    """JATS / HTML → plain text."""
    if not s:
        return None
    s = re.sub(r"<jats:title>\s*(abstract|summary)\s*</jats:title>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"^\s*(abstract|summary)[\s:.]+", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _date_from_parts(obj: dict | None) -> str | None:
    try:
        parts = obj["date-parts"][0]
    except (TypeError, KeyError, IndexError):
        return None
    parts = [p for p in parts if p]
    if not parts:
        return None
    return "-".join(f"{int(p):02d}" if i else str(p) for i, p in enumerate(parts))


def _inverted_to_text(idx: dict | None) -> str | None:
    if not idx:
        return None
    pos = {}
    for w, ps in idx.items():
        for p in ps:
            pos[p] = w
    return " ".join(pos[i] for i in sorted(pos)) or None


def _norm_license(lic: str | None) -> str | None:
    if not lic:
        return None
    lic = lic.lower()
    m = re.search(r"creativecommons\.org/(?:licenses|publicdomain)/([a-z\-]+)", lic)
    if m:
        return "cc0" if m.group(1) == "zero" else f"cc-{m.group(1)}"
    return lic


def _page_fields(first: str | None, last: str | None = None) -> dict:
    """Record page fields from a source's first and last page.

    Sources disagree on notation: article numbers given as pages (OpenAlex and Europe PMC
    write "Article5"), PubMed-style abbreviated ends (7792–7 for 7792–7797), and a last
    page that repeats the first.
    """
    if article := re.fullmatch(r"article\s*(?:no\.?\s*)?(\S+)", first or "", re.I):
        return {"first_page": None, "last_page": None, "article_number": article[1]}
    if first and last and first.isdigit() and last.isdigit() and len(last) < len(first):
        last = first[:len(first) - len(last)] + last
    return {"first_page": first or None, "last_page": last if last and last != first else None}


def _page_range(pages: str | None) -> dict:
    """`_page_fields` for a "first-last" string."""
    first, _, last = (pages or "").replace("–", "-").partition("-")
    return _page_fields(first, last)


# ---------------------------------------------------------------------------
# CrossRef
# ---------------------------------------------------------------------------

def crossref_work(http: Http, doi: str) -> dict | None:
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
    date = next(filter(None, (_date_from_parts(m.get(k)) for k in
                              ("published-print", "published-online", "issued", "published", "posted", "created"))), None)
    ctype = m.get("type") or "journal-article"
    is_preprint = ctype == "posted-content" and (m.get("subtype") in (None, "preprint"))
    journal = (m.get("container-title") or [None])[0]
    if is_preprint and not journal:
        journal = (m.get("institution") or [{}])[0].get("name") or m.get("publisher")
    related_doi, related_rel = next(((item["id"].lower(), r) for r in ("is-preprint-of", "has-preprint")
                                     for item in (m.get("relation") or {}).get(r) or []
                                     if item.get("id-type") == "doi"), (None, None))
    issn = next((t["value"] for t in m.get("issn-type") or [] if t.get("type") == "print"),
                (m.get("ISSN") or [None])[0])
    lic = next((_norm_license(entry.get("URL")) for entry in m.get("license") or []
                if entry.get("content-version") in ("vor", "unspecified")), None)
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
        "article_number": m.get("article-number"),
        **_page_range(m.get("page")),
        "issn": issn,
        "doi": (m.get("DOI") or "").lower() or None,
        "url": f"https://doi.org/{m['DOI']}" if m.get("DOI") else m.get("URL"),
        "abstract": strip_markup(m.get("abstract")),
        "keywords": m.get("subject") or [],
        "related_doi": related_doi,
        "related_relation": related_rel,
        "_license": lic,
    }


def crossref_search(http: Http, title: str, author: str | None = None, rows: int = 5) -> list[dict]:
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


def _openalex_params(http: Http, api_key: str | None) -> dict:
    p = {"mailto": http.mailto, "select": _OA_SELECT}
    if api_key:
        p["api_key"] = api_key
    return p


def openalex_work(http: Http, doi: str, api_key: str | None = None) -> dict | None:
    """The OpenAlex record of exactly this DOI, or None.

    OpenAlex merges a preprint and its published version into one work: looked up by the
    preprint's DOI it can return the journal article's record, whose volume, issue, PMID
    and OA status belong to another version.
    """
    body = http.get(f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi, safe='/')}",
                    params=_openalex_params(http, api_key))
    fields = openalex_to_fields(body) if body else None
    return fields if fields and fields["doi"] == doi.lower() else None


def openalex_to_fields(w: dict) -> dict:
    authors = [Author.from_display((a.get("author") or {}).get("display_name") or "")
               for a in w.get("authorships") or []]
    src = ((w.get("primary_location") or {}).get("source") or {})
    repository = src.get("type") == "repository"
    bib = w.get("biblio") or {}
    oa = w.get("open_access") or {}
    best = w.get("best_oa_location") or {}
    ids = w.get("ids") or {}
    pmid = (ids.get("pmid") or "").rsplit("/", 1)[-1] or None
    pmcid = (ids.get("pmcid") or "").rsplit("/", 1)[-1] or None
    date = w.get("publication_date")
    doi = (w.get("doi") or "").replace("https://doi.org/", "").lower() or None
    return {
        "title": w.get("title") or w.get("display_name"),
        "authors": [a for a in authors if a.family or a.literal],
        # For PubMed-only records the "source" is PubMed itself, which is not a journal.
        "journal": None if repository else src.get("display_name"),
        "issn": None if repository else src.get("issn_l"),
        "date": date,
        "year": date[:4] if date else None,
        "volume": bib.get("volume"),
        "issue": bib.get("issue"),
        **_page_fields(bib.get("first_page"), bib.get("last_page")),
        "doi": doi,
        # Older papers often have no DOI (e.g. Cancer Research before ~2004): link PubMed instead.
        "url": (f"https://doi.org/{doi}" if doi else
                f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None),
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


def openalex_search(http: Http, title: str, api_key: str | None = None, rows: int = 5) -> list[dict]:
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

def unpaywall(http: Http, doi: str, email: str) -> OpenAccess | None:
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


def arxiv_work(http: Http, arxiv_id: str) -> dict | None:
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

def europepmc(http: Http, doi: str | None = None, pmid: str | None = None) -> dict | None:
    """By DOI, or by PMID for papers without one (PubMed-indexed papers from before DOIs)."""
    query = f'DOI:"{doi}"' if doi else f"EXT_ID:{pmid} AND SRC:MED"
    body = http.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                    params={"query": query, "resultType": "core", "format": "json", "pageSize": 1})
    try:
        r = body["resultList"]["result"][0]
    except (TypeError, KeyError, IndexError):
        return None
    info = r.get("journalInfo") or {}
    journal = info.get("journal") or {}
    return {
        "abstract": strip_markup(r.get("abstractText")),
        "pmid": r.get("pmid"),
        "pmcid": r.get("pmcid"),
        "keywords": (r.get("keywordList") or {}).get("keyword") or [],
        "journal": journal.get("title"),       # NLM style ("Cancer research"); see journal_name
        "issn": journal.get("issn") or journal.get("essn"),
        "volume": info.get("volume"),
        "issue": info.get("issue"),
        **_page_range(r.get("pageInfo")),
    }


def journal_name(http: Http, issn: str) -> str | None:
    """The journal's own name for an ISSN: 'Cancer Research', where NLM writes 'Cancer research'."""
    body = http.get(f"https://api.openalex.org/sources/issn:{issn}",
                    params={"mailto": http.mailto, "select": "display_name"})
    return (body or {}).get("display_name")
