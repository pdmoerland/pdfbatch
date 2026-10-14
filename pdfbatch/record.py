"""Data model for one PDF → one reference record.

A reference manager needs structured author names, journal/volume/issue/pages,
item type and open-access details.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional


@dataclass
class Author:
    family: str = ""
    given: str = ""
    literal: str = ""          # organisations / names we could not split

    def ris(self) -> str:
        if self.family:
            return f"{self.family}, {self.given}".rstrip(", ")
        return self.literal

    @classmethod
    def from_display(cls, name: str) -> "Author":
        """Best-effort split of 'Given Middle Family' or 'Family, Given'."""
        name = " ".join((name or "").split())
        if not name:
            return cls()
        # Some sources write family names in capitals ("J. A. EARLE-HUGHES").
        uncap = lambda fam: fam.title() if fam.isupper() and len(fam) > 2 else fam
        if "," in name:
            fam, giv = name.split(",", 1)
            return cls(family=uncap(fam.strip()), given=giv.strip())
        parts = name.split(" ")
        if len(parts) == 1:
            return cls(literal=name)
        # Keep common particles with the family name ("van der Berg").
        particles = {"van", "von", "der", "den", "de", "del", "della", "da", "di", "la", "le", "ter", "ten"}
        i = len(parts) - 1
        while i > 1 and parts[i - 1].lower() in particles:
            i -= 1
        return cls(family=uncap(" ".join(parts[i:])), given=" ".join(parts[:i]))


@dataclass
class Link:
    kind: str                  # 'code' | 'data'
    platform: str
    url: str
    label: Optional[str] = None


@dataclass
class OpenAccess:
    is_oa: Optional[bool] = None
    status: Optional[str] = None        # gold | diamond | hybrid | bronze | green | closed
    license: Optional[str] = None       # e.g. cc-by
    version: Optional[str] = None       # publishedVersion | acceptedVersion | submittedVersion
    url: Optional[str] = None           # best free copy
    source: Optional[str] = None        # 'unpaywall' | 'openalex' | 'rule'


@dataclass
class Record:
    # provenance
    pdf_path: str = ""
    pdf_sha256: str = ""
    resolved_via: List[str] = field(default_factory=list)   # e.g. ['xmp-doi', 'crossref', 'openalex']
    confidence: str = "unresolved"      # high | medium | unresolved
    notes: List[str] = field(default_factory=list)          # human-readable warnings
    user_notes: Optional[str] = None    # yours, from notes.md; refreshed on every run

    # bibliographic
    item_type: Optional[str] = None     # CrossRef vocabulary
    title: Optional[str] = None
    authors: List[Author] = field(default_factory=list)
    journal: Optional[str] = None
    journal_abbrev: Optional[str] = None
    publisher: Optional[str] = None
    year: Optional[str] = None
    date: Optional[str] = None          # YYYY[-MM[-DD]]
    volume: Optional[str] = None
    issue: Optional[str] = None
    first_page: Optional[str] = None
    last_page: Optional[str] = None
    article_number: Optional[str] = None
    issn: Optional[str] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    url: Optional[str] = None
    abstract: Optional[str] = None
    abstract_source: Optional[str] = None
    keywords: List[str] = field(default_factory=list)

    # preprint ↔ published version
    is_preprint: bool = False
    related_doi: Optional[str] = None
    related_relation: Optional[str] = None   # 'is-preprint-of' | 'has-preprint'

    oa: OpenAccess = field(default_factory=OpenAccess)
    links: List[Link] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.confidence == "unresolved"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Record":
        d = dict(d)
        d["authors"] = [Author(**a) for a in d.get("authors") or []]
        d["links"] = [Link(**l) for l in d.get("links") or []]
        d["oa"] = OpenAccess(**(d.get("oa") or {}))
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in known})
