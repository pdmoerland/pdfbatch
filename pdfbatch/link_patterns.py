"""URL pattern registry — recognizes and normalizes code / data links.

Adding a new platform is typically <15 lines: extend REGISTRY below with a
`LinkPattern` and a `normalize` fn. Per-platform kill switches live in
`config.yaml → extractors.code_data_links.platforms`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple


# -------------------------------------------------------------------------
# Model
# -------------------------------------------------------------------------

@dataclass(frozen=True)
class LinkPattern:
    platform: str                              # 'github' etc.
    kind: str                                  # 'code' | 'data'
    domains: Tuple[str, ...]                   # lowercase domain substrings
    url_re: re.Pattern                         # full-URL matcher
    normalize: Callable[[str], str]            # canonicalization fn
    validate: bool = True                      # False → skip HEAD probing (DOIs)
    # URLs whose first user-slot path segment is in this set are not real
    # repos/models/datasets — they're reserved documentation or marketing
    # paths (e.g., `huggingface.co/docs/hub`, `github.com/features`).
    # Checked by `classify()` AFTER url_re matches.
    reserved_user_slots: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ClassifiedLink:
    """Output of `classify(url)` — a URL tagged with its pattern."""

    platform: str
    kind: str
    url: str                                   # normalized
    original_url: str                          # what we found in the paper
    pattern: LinkPattern


# -------------------------------------------------------------------------
# Normalization helpers
# -------------------------------------------------------------------------


def _strip_query_fragment(url: str) -> str:
    return url.split('#', 1)[0].split('?', 1)[0].rstrip('/')


def _normalize_github(url: str) -> str:
    """`github.com/org/repo/tree/main/src` → `https://github.com/org/repo`."""
    u = _strip_query_fragment(url)
    m = re.match(r'https?://(?:www\.)?github\.com/([^/]+)/([^/]+)', u)
    if not m:
        return u
    return f"https://github.com/{m.group(1)}/{m.group(2)}"


def _normalize_gitlab(url: str) -> str:
    """GitLab allows nested groups (`/a/b/c/repo`). The `/-/` segment marks
    the start of a sub-path (commits / trees / issues); strip everything at
    and after it. Also strip trailing empty `-` segments that older URL
    shortening might leave behind.
    """
    u = _strip_query_fragment(url)
    # Drop the "/-/..." tail used by GitLab for sub-paths
    u = re.sub(r'/\-(?:/.*)?$', '', u)
    m = re.match(r'https?://(?:www\.)?gitlab\.com/((?:[^/]+/){1,3}[^/]+)', u)
    if not m:
        return u
    path = m.group(1).rstrip('/-')
    return f"https://gitlab.com/{path}"


def _normalize_bitbucket(url: str) -> str:
    u = _strip_query_fragment(url)
    m = re.match(r'https?://(?:www\.)?bitbucket\.org/([^/]+)/([^/]+)', u)
    if not m:
        return u
    return f"https://bitbucket.org/{m.group(1)}/{m.group(2)}"


def _normalize_codeberg(url: str) -> str:
    u = _strip_query_fragment(url)
    m = re.match(r'https?://(?:www\.)?codeberg\.org/([^/]+)/([^/]+)', u)
    if not m:
        return u
    return f"https://codeberg.org/{m.group(1)}/{m.group(2)}"


def _normalize_huggingface(url: str) -> str:
    """Both `huggingface.co/user/repo` and `hf.co/user/repo` → canonical form.
    Handles `/datasets/<user>/<repo>` and `/spaces/<user>/<repo>` too."""
    u = _strip_query_fragment(url)
    u = u.replace('://hf.co/', '://huggingface.co/')
    m = re.match(r'https?://(?:www\.)?huggingface\.co/((?:datasets/|spaces/)?[^/]+/[^/]+)', u)
    if not m:
        return u
    return f"https://huggingface.co/{m.group(1)}"


def _normalize_zenodo(url: str) -> str:
    """Both `zenodo.org/record/NNNN` and `doi.org/10.5281/zenodo.NNNN` →
    canonical DOI `https://doi.org/10.5281/zenodo.NNNN`."""
    u = _strip_query_fragment(url)
    m = re.search(r'zenodo\.(?:org/record/|org/records/)(\d+)', u)
    if m:
        return f"https://doi.org/10.5281/zenodo.{m.group(1)}"
    m = re.search(r'doi\.org/(10\.5281/zenodo\.\d+)', u)
    if m:
        return f"https://doi.org/{m.group(1)}"
    return u


def _normalize_figshare(url: str) -> str:
    u = _strip_query_fragment(url)
    m = re.search(r'figshare\.com/(?:articles|s|projects)/(?:[^/]+/)?(\d+)', u)
    if m:
        return f"https://figshare.com/articles/{m.group(1)}"
    return u


def _normalize_dryad(url: str) -> str:
    u = _strip_query_fragment(url)
    m = re.search(r'(?:datadryad\.org|dryad)/(?:stash/)?(?:dataset/)?doi:(10\.\d+/[^/\s]+)', u)
    if m:
        return f"https://datadryad.org/dataset/doi:{m.group(1)}"
    return u


def _normalize_osf(url: str) -> str:
    u = _strip_query_fragment(url)
    m = re.search(r'osf\.io/([A-Za-z0-9]+)', u)
    if m:
        return f"https://osf.io/{m.group(1)}"
    return u


def _normalize_synapse(url: str) -> str:
    u = _strip_query_fragment(url)
    m = re.search(r'synapse\.org/(?:#!?Synapse:|)(syn\d+)', u, re.I)
    if m:
        return f"https://www.synapse.org/#!Synapse:{m.group(1)}"
    return u


# -------------------------------------------------------------------------
# Registry  (order matters: first match wins)
# -------------------------------------------------------------------------

REGISTRY: List[LinkPattern] = [
    # --- Code platforms (HEAD-validated) ---
    LinkPattern(
        platform='github', kind='code',
        domains=('github.com',),
        url_re=re.compile(r'https?://(?:www\.)?github\.com/[^/\s]+/[^/\s#?]+', re.I),
        normalize=_normalize_github,
        validate=True,
        # Reserved paths on github.com that aren't repos:
        # site navigation, marketing, meta features, topic browse.
        reserved_user_slots=(
            'about', 'codespaces', 'customer-stories', 'enterprise', 'events',
            'explore', 'features', 'issues', 'login', 'logout', 'marketplace',
            'new', 'notifications', 'orgs', 'organizations', 'password_reset',
            'pricing', 'pulls', 'readme', 'search', 'security', 'sessions',
            'settings', 'signup', 'site', 'sponsors', 'stars', 'topics', 'trending',
        ),
    ),
    LinkPattern(
        platform='gitlab', kind='code',
        domains=('gitlab.com',),
        url_re=re.compile(r'https?://(?:www\.)?gitlab\.com/(?:[^/\s]+/){1,3}[^/\s#?]+', re.I),
        normalize=_normalize_gitlab,
        validate=True,
        reserved_user_slots=(
            'admin', 'dashboard', 'explore', 'help', 'search', 'users', 'pricing',
            'features', 'solutions', 'customers', 'blog',
        ),
    ),
    LinkPattern(
        platform='bitbucket', kind='code',
        domains=('bitbucket.org',),
        url_re=re.compile(r'https?://(?:www\.)?bitbucket\.org/[^/\s]+/[^/\s#?]+', re.I),
        normalize=_normalize_bitbucket,
        validate=True,
        reserved_user_slots=(
            'product', 'pricing', 'enterprise', 'blog', 'support', 'account',
            'dashboard', 'repo', 'snippets',
        ),
    ),
    LinkPattern(
        platform='codeberg', kind='code',
        domains=('codeberg.org',),
        url_re=re.compile(r'https?://(?:www\.)?codeberg\.org/[^/\s]+/[^/\s#?]+', re.I),
        normalize=_normalize_codeberg,
        validate=True,
        reserved_user_slots=(
            'admin', 'explore', 'help', 'issues', 'search', 'user', 'signup', 'login',
        ),
    ),

    # --- Data platforms ---
    # HuggingFace (models + datasets + spaces) — validate (content can be deleted).
    LinkPattern(
        platform='huggingface', kind='data',
        domains=('huggingface.co', 'hf.co'),
        url_re=re.compile(
            r'https?://(?:www\.)?(?:huggingface\.co|hf\.co)/(?:datasets/|spaces/)?[^/\s]+/[^/\s#?]+',
            re.I,
        ),
        normalize=_normalize_huggingface,
        validate=True,
        # Reserved first-path segments on huggingface.co that aren't models,
        # datasets, or spaces. `datasets` and `spaces` ARE real content-type
        # prefixes — classify() strips those before checking this set.
        reserved_user_slots=(
            'about', 'blog', 'chat', 'course', 'docs', 'enterprise', 'inference-api',
            'inference-endpoints', 'join', 'learn', 'login', 'new', 'new-dataset',
            'new-space', 'organizations', 'papers', 'pricing', 'privacy', 'pro',
            'settings', 'support', 'tasks', 'terms', 'welcome',
            # These are also valid path prefixes but the URL `huggingface.co/models`
            # alone (no user segment after) means the browse page.
            'models', 'datasets', 'spaces',
        ),
    ),
    # Zenodo — DOI-based, skip validation.
    LinkPattern(
        platform='zenodo', kind='data',
        domains=('zenodo.org', 'doi.org'),   # doi.org matches many things; filter by regex
        url_re=re.compile(
            r'https?://(?:(?:www\.)?zenodo\.org/records?/\d+|doi\.org/10\.5281/zenodo\.\d+)',
            re.I,
        ),
        normalize=_normalize_zenodo,
        validate=False,
    ),
    # figshare — DOI-based + site URL.
    LinkPattern(
        platform='figshare', kind='data',
        domains=('figshare.com', 'doi.org'),
        url_re=re.compile(
            r'https?://(?:(?:www\.)?figshare\.com/(?:articles|s|projects)/\S+|doi\.org/10\.6084/m9\.figshare\.\d+)',
            re.I,
        ),
        normalize=_normalize_figshare,
        validate=False,
    ),
    # Dryad
    LinkPattern(
        platform='dryad', kind='data',
        domains=('datadryad.org', 'dryad', 'doi.org'),
        url_re=re.compile(
            r'https?://(?:(?:www\.)?datadryad\.org/\S+|doi\.org/10\.5061/dryad\.\S+)',
            re.I,
        ),
        normalize=_normalize_dryad,
        validate=False,
    ),
    # OSF
    LinkPattern(
        platform='osf', kind='data',
        domains=('osf.io',),
        url_re=re.compile(r'https?://osf\.io/[A-Za-z0-9]{5,}', re.I),
        normalize=_normalize_osf,
        validate=False,
    ),
    # Synapse
    LinkPattern(
        platform='synapse', kind='data',
        domains=('synapse.org',),
        url_re=re.compile(r'https?://(?:www\.)?synapse\.org/[^\s]*syn\d+', re.I),
        normalize=_normalize_synapse,
        validate=False,
    ),
]


# -------------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------------


def _user_slot(url: str, platform: str) -> Optional[str]:
    """Return the user/org path segment of a URL, or None.

    For HuggingFace, URLs may be prefixed with `/datasets/` or `/spaces/` —
    those are content-type prefixes, NOT user names, so we skip them.
    For every other platform, the user slot is the first path segment.
    """
    u = url.split('://', 1)[-1]
    parts = u.split('/', 2)[1:]        # drop host; keep path-only segments
    if not parts:
        return None
    first = parts[0].lower()
    if platform == 'huggingface' and first in ('datasets', 'spaces'):
        if len(parts) >= 2:
            return parts[1].lower()
        return None
    return first


def classify(url: str) -> Optional[ClassifiedLink]:
    """Return a `ClassifiedLink` if the URL matches any known pattern, else None.

    Rejects URLs that match a pattern's `url_re` but whose first user-slot
    path segment is in the pattern's `reserved_user_slots` (e.g.
    `huggingface.co/docs/hub` matches the HF regex but `docs` is reserved
    documentation, not a model repo).

    The original URL is preserved on `ClassifiedLink.original_url`; the
    `url` field carries the normalized (canonical) form that goes into
    `PaperMetadata.external_links` after de-dup.
    """
    for pat in REGISTRY:
        if not pat.url_re.match(url):
            continue
        # Reserved-path gate — real-repo URLs only.
        if pat.reserved_user_slots:
            slot = _user_slot(url, pat.platform)
            if slot is not None and slot in pat.reserved_user_slots:
                # Don't try other patterns; a URL like github.com/features
                # that matches the GitHub regex isn't going to match Zenodo.
                return None
        try:
            return ClassifiedLink(
                platform=pat.platform,
                kind=pat.kind,
                url=pat.normalize(url),
                original_url=url,
                pattern=pat,
            )
        except Exception:                       # noqa: BLE001
            # Normalize should never raise, but on the off chance it does
            # fall back to unnormalized matching.
            return ClassifiedLink(
                platform=pat.platform, kind=pat.kind,
                url=url, original_url=url, pattern=pat,
            )
    return None


def extract_from_text(text: str) -> List[ClassifiedLink]:
    """Scan free-text content (HTML-stripped, fulltext XML, etc.) for all URLs
    matching any registered pattern.

    De-duplicates by normalized URL; preserves first-occurrence order so the
    subsequent Slack rendering stays stable.
    """
    if not text:
        return []

    # Grab every http(s) URL; then classify each.
    seen_norm: set[str] = set()
    out: List[ClassifiedLink] = []

    # Loose URL matcher. We're deliberately permissive; classify() rejects
    # anything that isn't an interesting platform.
    for m in re.finditer(r'https?://[^\s<>"\'\])]+', text):
        raw = m.group(0).rstrip('.,;:!?)')        # strip trailing punctuation
        cl = classify(raw)
        if cl is None:
            continue
        if cl.url in seen_norm:
            continue
        seen_norm.add(cl.url)
        out.append(cl)

    return out


def platform_enabled(platform: str, config_platforms: dict) -> bool:
    """Check the `extractors.code_data_links.platforms.<name>` config flag.

    Missing key defaults to `True` (opt-out, not opt-in).
    """
    return bool((config_platforms or {}).get(platform, True))


# URLs that are scholarly-site chrome or generic frameworks cited by many papers;
# they add noise rather than pointing at a paper's own code/data.
BLOCKLIST_URLS = [
    # ar5iv infrastructure (leaked into extraction from ar5iv's footer pre-scoping)
    'https://github.com/dginev/ar5iv',
    # arXiv infrastructure
    'https://github.com/arxiv/arxiv-browse',
    'https://github.com/arxiv/arxiv-docs',
    # Framework / library repos — cited by many papers, rarely the paper's own code
    'https://github.com/tensorflow/tensor2tensor',
    'https://github.com/tensorflow/tensorflow',
    'https://github.com/pytorch/pytorch',
    'https://github.com/huggingface/transformers',
    'https://github.com/huggingface/datasets',
    'https://github.com/scikit-learn/scikit-learn',
    'https://github.com/numpy/numpy',
    'https://github.com/scipy/scipy',
    'https://github.com/pandas-dev/pandas',
    'https://github.com/jax-ml/jax',
    'https://github.com/google/jax',
]
