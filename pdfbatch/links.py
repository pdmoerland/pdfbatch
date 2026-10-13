"""The paper's own code and data links, found in its full text and link annotations."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import diskcache
import requests

from .identifiers import REFS_HEADING_RE
from .pdf_reader import PdfContent
from .record import Link

MAX_PER_KIND = 5


# ---------------------------------------------------------------------------
# platforms
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Platform:
    name: str
    kind: str                                  # 'code' | 'data'
    url_re: re.Pattern
    canonical: Callable[[str], str]
    probe: bool = True                         # False: DOI-registered, permanent by contract
    reserved: frozenset = field(default_factory=frozenset)   # first path segments that are site pages


@dataclass(frozen=True)
class Found:
    url: str                                   # canonical
    original: str                              # as printed in the paper
    platform: Platform

    @property
    def kind(self) -> str:
        return self.platform.kind


def _canon(pattern: str, template: str) -> Callable[[str], str]:
    """Canonicaliser: fill `template` with the groups of `pattern`, else return the URL as is."""
    rx = re.compile(pattern, re.I)

    def canonical(url: str) -> str:
        url = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        m = rx.search(url)
        return template.format(*m.groups()) if m else url
    return canonical


def _gitlab(url: str) -> str:
    """GitLab nests groups (`/a/b/c/repo`); `/-/` starts a sub-path (tree, issues, …)."""
    url = re.sub(r"/-(?:/.*)?$", "", url.split("#", 1)[0].split("?", 1)[0].rstrip("/"))
    m = re.search(r"gitlab\.com/((?:[^/]+/){1,3}[^/]+)", url, re.I)
    return f"https://gitlab.com/{m.group(1).rstrip('/-')}" if m else url


def _repo_host(name: str, host: str, reserved: str) -> Platform:
    h = re.escape(host)
    return Platform(name, "code", re.compile(rf"https?://(?:www\.)?{h}/[^/\s]+/[^/\s#?]+", re.I),
                    _canon(rf"{h}/([^/]+)/([^/]+)", f"https://{host}/{{}}/{{}}"),
                    reserved=frozenset(reserved.split()))


PLATFORMS: List[Platform] = [          # order matters: first match wins
    _repo_host("github", "github.com",
               "about codespaces customer-stories enterprise events explore features issues login "
               "logout marketplace new notifications orgs organizations password_reset pricing pulls "
               "readme search security sessions settings signup site sponsors stars topics trending"),
    Platform("gitlab", "code",
             re.compile(r"https?://(?:www\.)?gitlab\.com/(?:[^/\s]+/){1,3}[^/\s#?]+", re.I), _gitlab,
             reserved=frozenset("admin dashboard explore help search users pricing features solutions "
                                "customers blog".split())),
    _repo_host("bitbucket", "bitbucket.org",
               "product pricing enterprise blog support account dashboard repo snippets"),
    _repo_host("codeberg", "codeberg.org", "admin explore help issues search user signup login"),
    Platform("huggingface", "data",
             re.compile(r"https?://(?:www\.)?(?:huggingface|hf)\.co/(?:datasets/|spaces/)?[^/\s]+/[^/\s#?]+", re.I),
             _canon(r"(?:huggingface|hf)\.co/((?:datasets/|spaces/)?[^/]+/[^/]+)", "https://huggingface.co/{}"),
             reserved=frozenset("about blog chat course docs enterprise inference-api inference-endpoints "
                                "join learn login new new-dataset new-space organizations papers pricing "
                                "privacy pro settings support tasks terms welcome models datasets spaces".split())),
    Platform("zenodo", "data",
             re.compile(r"https?://(?:(?:www\.)?zenodo\.org/records?/\d+|doi\.org/10\.5281/zenodo\.\d+)", re.I),
             _canon(r"(?:zenodo\.org/records?/|doi\.org/10\.5281/zenodo\.)(\d+)", "https://doi.org/10.5281/zenodo.{}"),
             probe=False),
    Platform("figshare", "data",
             re.compile(r"https?://(?:(?:www\.)?figshare\.com/(?:articles|s|projects)/\S+|doi\.org/10\.6084/m9\.figshare\.\d+)", re.I),
             _canon(r"figshare\.com/(?:articles|s|projects)/(?:[^/]+/)?(\d+)", "https://figshare.com/articles/{}"),
             probe=False),
    Platform("dryad", "data",
             re.compile(r"https?://(?:(?:www\.)?datadryad\.org/\S+|doi\.org/10\.5061/dryad\.\S+)", re.I),
             _canon(r"(?:datadryad\.org|dryad)/(?:stash/)?(?:dataset/)?doi:(10\.\d+/[^/\s]+)",
                    "https://datadryad.org/dataset/doi:{}"),
             probe=False),
    Platform("osf", "data", re.compile(r"https?://osf\.io/[A-Za-z0-9]{5,}", re.I),
             _canon(r"osf\.io/([A-Za-z0-9]+)", "https://osf.io/{}"), probe=False),
    Platform("synapse", "data", re.compile(r"https?://(?:www\.)?synapse\.org/[^\s]*syn\d+", re.I),
             _canon(r"synapse\.org/(?:#!?Synapse:|)(syn\d+)", "https://www.synapse.org/#!Synapse:{}"),
             probe=False),
]

# Site chrome and frameworks that many papers cite but that are rarely the paper's own code.
BLOCKLIST = frozenset(f"https://github.com/{r}" for r in (
    "dginev/ar5iv", "arxiv/arxiv-browse", "arxiv/arxiv-docs", "tensorflow/tensor2tensor",
    "tensorflow/tensorflow", "pytorch/pytorch", "huggingface/transformers", "huggingface/datasets",
    "scikit-learn/scikit-learn", "numpy/numpy", "scipy/scipy", "pandas-dev/pandas", "jax-ml/jax",
    "google/jax"))


def classify(url: str) -> Optional[Found]:
    for p in PLATFORMS:
        if not p.url_re.match(url):
            continue
        path = url.split("://", 1)[-1].split("/")[1:]
        if p.name == "huggingface" and path[0].lower() in ("datasets", "spaces"):
            path = path[1:]
        if path and path[0].lower() in p.reserved:
            return None                        # e.g. github.com/features: a site page, not a repo
        return Found(p.canonical(url), url, p)
    return None


def _first_per_url(found: Iterable[Optional[Found]]) -> List[Found]:
    first: Dict[str, Found] = {}
    for f in found:
        if f:
            first.setdefault(f.url, f)
    return list(first.values())


def extract(text: str) -> List[Found]:
    """Every recognised link in `text`, once per canonical URL, in order of appearance."""
    return _first_per_url(classify(m.group(0).rstrip(".,;:!?)"))
                          for m in re.finditer(r"https?://[^\s<>\"'\])]+", text))


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------

_TAGS = ["Code", "Data", "Dataset", "Datasets", "Model", "Models", "Weights", "Repo", "Repository",
         "Website", "Project page", "Project", "HuggingFace", "GitHub", "GitLab", "Bitbucket",
         "Codeberg", "Zenodo", "Demo", "Checkpoints"]
_TAG_RE = re.compile(r"\b(?P<label>%s)\s*[:\-–]\s*<?(?P<url>https?://\S+)"
                     % "|".join(sorted(map(re.escape, _TAGS), key=len, reverse=True)), re.I)
_TRIGGERS = [(re.compile(rx, re.I), label) for rx, label in [      # most specific first
    (r"\bCode\s+(?:is|are|will\s+be)?\s*available\s+at\b", "Code"),
    (r"\bCode\s+availability\b", "Code"),
    (r"\bSource\s+code\s+(?:is\s+)?(?:available|at)\b", "Code"),
    (r"\bData\s+(?:are|is|will\s+be)\s+available\s+at\b", "Data"),
    (r"\bData\s+availability\b", "Data"),
    (r"\bPretrained\s+(?:model|weights|checkpoints)\b", "Pretrained model"),
    (r"\bTrained\s+models?\s+(?:is|are|will\s+be)?\s*released\s+at\b", "Trained model"),
    (r"\bRepository\b", "Repository"),
    (r"\bProject\s+page\b", "Project page"),
    (r"\bDataset\s+(?:is|are|will\s+be)\s*available\b", "Dataset"),
]]
_PRETTY = {"huggingface": "HuggingFace", "bitbucket": "Bitbucket", "codeberg": "Codeberg"}
# Labels that say no more than the link's kind or platform.
_UNINFORMATIVE = {"code", "data", "repository", "github", "gitlab", "zenodo"}


def _position(text: str, f: Found) -> int:
    for needle in (f.original, f.url, f.original[:40]):
        i = text.find(needle)
        if i >= 0:
            return i
    return -1


def labels(text: str, links: Sequence[Found], window: int = 200) -> Dict[str, Optional[str]]:
    """`{url: label}` from a tag in front of the URL ("Weights: https://…"), else from a
    trigger phrase in the `window` characters before it ("Data are available at")."""
    out: Dict[str, Optional[str]] = dict.fromkeys(f.url for f in links)
    for m in _TAG_RE.finditer(text):
        url = m.group("url").rstrip(".,;:!?)>")
        for f in links:
            if out[f.url] is None and (f.url in url or f.original in url):
                label = m.group("label").strip()
                out[f.url] = _PRETTY.get(label.lower(), label[:1].upper() + label[1:])
    for f in links:
        i = _position(text, f)
        if out[f.url] is None and i >= 0:
            out[f.url] = next((lab for rx, lab in _TRIGGERS if rx.search(text[max(0, i - window):i])), None)
    return {u: None if lab and lab.lower() in _UNINFORMATIVE else lab for u, lab in out.items()}


# ---------------------------------------------------------------------------
# liveness
# ---------------------------------------------------------------------------

class LinkChecker:
    """HEAD-checks links on platforms where content can disappear; cached for 30 days."""

    def __init__(self, cache_dir: Path, ttl_days: int = 30, timeout: float = 3.0):
        self.cache = diskcache.Cache(str(cache_dir))
        self.ttl = ttl_days * 86400
        self.timeout = timeout

    def alive(self, f: Found) -> bool:
        if not f.platform.probe:
            return True
        ok = self.cache.get(f.url)
        if ok is None:
            ok = self._head(f.url)
            self.cache.set(f.url, ok, expire=self.ttl)
        return ok

    def _head(self, url: str) -> bool:
        try:
            r = requests.head(url, timeout=self.timeout, allow_redirects=True,     # GitHub refuses HEAD without a UA
                              headers={"User-Agent": "pdfbatch-link-check/1.0", "Accept": "*/*"})
        except requests.RequestException:
            return False
        return r.status_code < 400 or r.status_code == 429                        # 429: rate-limited, not dead


# ---------------------------------------------------------------------------
# the paper's own links
# ---------------------------------------------------------------------------

# What a sentence says about a URL right before it: the paper offers it ("is available at",
# "deposited in Zenodo at") or uses someone else's ("downloaded from", "based on").
_STATEMENT = re.compile(
    r"\b(?P<claim>availability|available|accessible|deposited|archived|released|hosted|"
    r"can be (?:found|accessed|downloaded|obtained))\b"
    r"|\b(?:(?:downloaded|obtained|retrieved|taken|adapted) from|provided (?:at|by|in)|based on|implemented in)\b",
    re.I)
_TAG_BEFORE = re.compile(r"\b(?:%s)\s*[:\-–]\s*<?$" % "|".join(map(re.escape, _TAGS)), re.I)


def _claimed(text: str, i: int, window: int = 300) -> bool:
    """The paper offers the link at `i` as its own: in the URL's own sentence, the statement
    nearest to it says "available at" or "deposited at", not "downloaded from", or a tag
    precedes it ("Code: https://…"). Tools a paper merely cites or compares, in tables or
    in passing, make no such claim (Chen et al.'s table of benchmarked methods)."""
    before = text[max(0, i - window):i]
    sentence = before[max((m.end() for m in re.finditer(r"[.!?]\s|\n\s*\n", before)), default=0):]
    statements = list(_STATEMENT.finditer(sentence))
    return bool(_TAG_BEFORE.search(sentence) or statements and statements[-1]["claim"])


_AFTER_REFS = re.compile(
    r"^\s*(?:\d+\.?\s*)?(methods|online methods|materials and methods|data availability|"
    r"code availability|data and code availability|resource availability|acknowledg(?:e)?ments?|"
    r"author contributions|supplementary|appendix|competing interests|extended data|"
    r"key resources table|star\s*methods)\b",
    re.I | re.M,
)


def find_links(pdf: PdfContent, checker: Optional[LinkChecker] = None) -> List[Link]:
    # Drop zero-width spaces, which some publishers put inside printed URLs as break points
    # ("https://​zenodo.​org/…"), and re-join URLs broken at line ends: after / - _
    # ("…/org/\nrepo"), and after a dot only when the next line goes on in lower case
    # ("github.\ncom"). A capital after a dot starts a new sentence or heading: "…/GLUE.\nResults".
    text = re.sub(r"[​-‍⁠﻿]", "", pdf.full_text)
    text = re.sub(r"(https?://\S*(?:[/\-_](?=\n[A-Za-z0-9])|\.(?=\n[a-z0-9])))\n", r"\1", text)
    refs = [(m.start(), (n.start() if (n := _AFTER_REFS.search(text, m.end())) else len(text)))
            for m in REFS_HEADING_RE.finditer(text)]

    # A URL broken across lines (in preprints, around a margin line number) leaves a prefix of
    # the hyperlink behind it in the text: ".../STARN" for ".../STARNet". The link annotation
    # is the authoritative form; a text URL that only prefixes an annotated one is dropped,
    # but its place in the text still locates the hyperlink.
    in_text, annotated = extract(text), extract("\n".join(pdf.link_uris))
    hyperlinks = {f.url for f in annotated}
    where = {f.url: _position(text, f) for f in in_text}
    for f in annotated:
        if where.get(f.url, -1) < 0:
            where[f.url] = next((where[g.url] for g in in_text if f.url.startswith(g.url)), -1)

    def own(f: Found) -> bool:
        i = where[f.url]
        cited = any(a <= i < b for a, b in refs)                  # in the reference list
        return (i >= 0 and not cited and _claimed(text, i) and f.url.lower() not in BLOCKLIST
                and (checker is None or checker.alive(f)))

    in_text = [f for f in in_text if f.url in hyperlinks or not any(u.startswith(f.url) for u in hyperlinks)]
    found = [f for f in _first_per_url(in_text + annotated) if own(f)]
    label = labels(text, found)
    seen = Counter()
    out = []
    for f in found:
        seen[f.kind] += 1
        if seen[f.kind] <= MAX_PER_KIND:
            out.append(Link(kind=f.kind, platform=f.platform.name, url=f.url, label=label[f.url]))
    return out
