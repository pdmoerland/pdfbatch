"""Data model for one PDF → one reference record.

A reference manager needs structured author names, journal/volume/issue/pages,
item type and open-access details.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

# The outcome for the PDF: `unresolved` means no API record validated. Not the same
# vocabulary as pipeline.Verdict, a verdict on one candidate record (`low`: not this PDF).
Confidence = Literal["high", "medium", "unresolved"]


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
    def from_display(cls, name: str) -> Author:
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
    label: str | None = None


@dataclass
class OpenAccess:
    is_oa: bool | None = None
    status: str | None = None        # gold | diamond | hybrid | bronze | green | closed
    license: str | None = None       # e.g. cc-by
    version: str | None = None       # publishedVersion | acceptedVersion | submittedVersion
    url: str | None = None           # best free copy
    source: str | None = None        # 'unpaywall' | 'openalex' | 'rule'

    @classmethod
    def preprint_server(cls, url: str | None) -> OpenAccess:
        """The rule for arXiv, bioRxiv and the like: green OA by definition, as submitted."""
        return cls(is_oa=True, status="green", version="submittedVersion", url=url, source="rule")


@dataclass
class Record:
    # provenance
    pdf_path: str = ""
    pdf_sha256: str = ""
    resolved_via: list[str] = field(default_factory=list)   # e.g. ['xmp-doi', 'crossref', 'openalex']
    confidence: Confidence = "unresolved"
    notes: list[str] = field(default_factory=list)          # human-readable warnings
    user_notes: str | None = None    # yours, from notes.md; refreshed on every run

    # bibliographic
    item_type: str | None = None     # CrossRef vocabulary
    title: str | None = None
    authors: list[Author] = field(default_factory=list)
    journal: str | None = None
    journal_abbrev: str | None = None
    publisher: str | None = None
    year: str | None = None
    date: str | None = None          # YYYY[-MM[-DD]]
    volume: str | None = None
    issue: str | None = None
    first_page: str | None = None
    last_page: str | None = None
    article_number: str | None = None
    issn: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    url: str | None = None
    abstract: str | None = None
    abstract_source: str | None = None
    keywords: list[str] = field(default_factory=list)

    # preprint ↔ published version
    is_preprint: bool = False
    related_doi: str | None = None
    related_relation: str | None = None   # 'is-preprint-of' | 'has-preprint'

    oa: OpenAccess = field(default_factory=OpenAccess)
    links: list[Link] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.confidence == "unresolved"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Record:
        d = dict(d)
        d["authors"] = [Author(**a) for a in d.get("authors") or []]
        d["links"] = [Link(**link) for link in d.get("links") or []]
        d["oa"] = OpenAccess(**(d.get("oa") or {}))
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in known})
