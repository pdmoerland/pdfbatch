"""HEAD-probe link validator with a 30-day diskcache TTL.

Skips probing for DOI-based platforms (Zenodo / figshare / Dryad / OSF /
Synapse) — those are permanent by registration contract. Code platforms
(GitHub / GitLab / Bitbucket / Codeberg / HuggingFace) do rot, so we probe
and cache the result.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import requests

from .link_patterns import ClassifiedLink


class LinkValidator:
    def __init__(
        self,
        *,
        cache_dir: Path,
        ttl_days: int = 30,
        timeout_s: float = 3.0,
    ):
        self._cache_dir = cache_dir
        self._cache = self._make_cache(cache_dir)
        self._ttl = ttl_days * 86400
        self._timeout = timeout_s
        self._log = logging.getLogger(__name__)

    def is_alive(self, link: ClassifiedLink) -> bool:
        """Return True if the link should be posted.

        Platforms where `pattern.validate is False` (DOI-based) always
        pass — their permanence is part of the registration contract.
        For the rest, HEAD probe + diskcache.
        """
        if not link.pattern.validate:
            return True

        key = f'link:{link.url}'
        if self._cache is not None:
            cached = self._cache.get(key)
            if cached is not None:
                return cached

        alive = self._probe(link.url)
        if self._cache is not None:
            self._cache.set(key, alive, expire=self._ttl)
        return alive

    def _probe(self, url: str) -> bool:
        try:
            r = requests.head(
                url,
                timeout=self._timeout,
                allow_redirects=True,
                headers={
                    # GitHub + many others refuse HEAD with no UA.
                    'User-Agent': 'pdfbatch-link-validator/1.0',
                    'Accept': '*/*',
                },
            )
        except requests.RequestException as e:
            self._log.info(f'[validator] {url} HEAD failed: {e}')
            return False

        # 2xx + 3xx = alive. Treat 429 as alive (we were rate-limited, not dead).
        if r.status_code < 400:
            return True
        if r.status_code == 429:
            return True
        self._log.info(f'[validator] {url} HEAD {r.status_code} → dead')
        return False

    @staticmethod
    def _make_cache(cache_dir: Path):
        try:
            import diskcache
        except ImportError:                 # diskcache is always installed per pyproject
            return None
        cache_dir.mkdir(parents=True, exist_ok=True)
        return diskcache.Cache(str(cache_dir))
