#!/usr/bin/env python3
"""Run pdfbatch from a checkout: `uv run pdf_batch.py ~/papers` (same as `pdf-batch`)."""

import sys

from pdfbatch.cli import main

if __name__ == "__main__":
    sys.exit(main())
