"""Cheap, rule-based labeler for code/data links.

Two rules:

1. **Tag-prefix** — papers (especially arXiv preprints) often write something
   like `GitHub: https://github.com/org/repo` or `Data: ...`. When we see
   the URL preceded by a recognized tag in the same line, we use the tag
   as the label.

2. **Sentence-window** — scan the ~200 chars of text before the URL for a
   trigger phrase (`Data are available at`, `Code availability`, etc.) and
   use the short label associated with that trigger.

Returns `{classified_link.url → label|None}`. Unlabeled entries fall through
to the LLM labeler (for data links; code links have no need for a label).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

from .link_patterns import ClassifiedLink


# Default label keywords; overridable via
# `extractors.code_data_links.labeler.heuristic.label_keywords`.
DEFAULT_LABEL_KEYWORDS: List[str] = [
    'Code', 'Data', 'Dataset', 'Datasets', 'Model', 'Models', 'Weights',
    'Repo', 'Repository', 'Website', 'Project page', 'Project',
    'HuggingFace', 'GitHub', 'GitLab', 'Bitbucket', 'Codeberg', 'Zenodo',
    'Demo', 'Checkpoints',
]


# Sentence-window triggers: ordered, most-specific first. Each item is
# (trigger-regex, derived-label).
DEFAULT_TRIGGERS: List[tuple[str, str]] = [
    (r'\bCode\s+(?:is|are|will\s+be)?\s*available\s+at\b', 'Code'),
    (r'\bCode\s+availability\b',                             'Code'),
    (r'\bSource\s+code\s+(?:is\s+)?(?:available|at)\b',     'Code'),
    (r'\bData\s+(?:are|is|will\s+be)\s+available\s+at\b',   'Data'),
    (r'\bData\s+availability\b',                             'Data'),
    (r'\bPretrained\s+(?:model|weights|checkpoints)\b',     'Pretrained model'),
    (r'\bTrained\s+model(?:s)?\s+(?:is|are|will\s+be)?\s*released\s+at\b', 'Trained model'),
    (r'\bRepository\b',                                      'Repository'),
    (r'\bProject\s+page\b',                                  'Project page'),
    (r'\bDataset\s+(?:is|are|will\s+be)\s*available\b',      'Dataset'),
]


class HeuristicLabeler:
    def __init__(
        self,
        *,
        label_keywords: Optional[List[str]] = None,
        sentence_window: int = 200,
    ):
        kws = label_keywords or DEFAULT_LABEL_KEYWORDS
        # Case-insensitive match; longest first to avoid "Code" matching inside
        # "Codeberg" etc.
        alt = '|'.join(sorted((re.escape(k) for k in kws), key=len, reverse=True))
        self._tag_re = re.compile(
            rf'\b(?P<label>{alt})\s*[:\-\u2013]\s*<?(?P<url>https?://\S+)',
            re.I,
        )
        self._triggers = [
            (re.compile(pat, re.I), label) for pat, label in DEFAULT_TRIGGERS
        ]
        self._sentence_window = sentence_window

    def label(
        self,
        text: str,
        links: Sequence[ClassifiedLink],
    ) -> Dict[str, Optional[str]]:
        """Return `{normalized_url → label|None}` for every link in `links`.

        Algorithm:
          1. Run the tag-prefix regex over the full text; bucket matches by
             URL (lexical match against link.original_url OR link.url).
          2. For any link that didn't get a label in step 1, scan the
             sentence-window preceding the URL's first occurrence in text.
        """
        out: Dict[str, Optional[str]] = {l.url: None for l in links}
        if not text or not links:
            return out

        # Step 1: tag-prefix matches
        for m in self._tag_re.finditer(text):
            label = m.group('label').strip()
            url = m.group('url').rstrip('.,;:!?)').rstrip('>')
            # Match either the normalized or the original URL
            for cl in links:
                if cl.url == url or cl.original_url == url or cl.url in url or cl.original_url in url:
                    if out.get(cl.url) is None:
                        out[cl.url] = _canonicalize_label(label)

        # Step 2: sentence-window triggers for still-unlabeled links
        for cl in links:
            if out.get(cl.url) is not None:
                continue
            # Find the first occurrence of either URL form in the text
            idx = text.find(cl.original_url)
            if idx < 0:
                idx = text.find(cl.url)
            if idx < 0:
                continue
            window_start = max(0, idx - self._sentence_window)
            window = text[window_start:idx]
            for trigger_re, label in self._triggers:
                if trigger_re.search(window):
                    out[cl.url] = label
                    break

        return out


def _canonicalize_label(label: str) -> str:
    """Normalize tag-prefix labels to a tidy display form.

    `github` / `GITHUB` → `GitHub`; `dataset` → `Dataset`; etc.
    """
    pretty = {
        'github': 'GitHub', 'gitlab': 'GitLab',
        'bitbucket': 'Bitbucket', 'codeberg': 'Codeberg',
        'huggingface': 'HuggingFace', 'zenodo': 'Zenodo',
    }
    key = label.lower()
    if key in pretty:
        return pretty[key]
    return label[:1].upper() + label[1:]
