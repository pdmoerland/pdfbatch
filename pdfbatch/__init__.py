"""Batch PDF to metadata (.md / .ris / .json), DOI-first."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pdfbatch")          # one version string, from pyproject.toml
except PackageNotFoundError:                   # running from a checkout that is not installed
    __version__ = "0"
