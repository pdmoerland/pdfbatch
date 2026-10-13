"""Synthetic PDFs + API responses in the real CrossRef / OpenAlex / arXiv /
Unpaywall / Europe PMC shapes, so the whole pipeline runs offline."""

from __future__ import annotations

from pathlib import Path

import pymupdf

LOREM = ("We present an approach that combines single-cell chromatin accessibility with spatial "
         "transcriptomics to map regulatory syntax across developmental time. Using this resource "
         "we identify enhancer grammars that predict cell-type specific expression and validate "
         "them in vivo, providing a framework for interpreting non-coding variation in the heart.")


def _page(doc, items):
    page = doc.new_page(width=595, height=842)
    for (x, y, text, size, *rot) in items:
        page.insert_text((x, y), text, fontsize=size, rotate=rot[0] if rot else 0)
    return page


def _para(text, width=90):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur); cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    return lines


def make_pdfs(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)

    # A — journal article: DOI in the running header, a decoy DOI cited on page 1,
    #     own code/data in availability statements, cited tools in references.
    doc = pymupdf.open()
    items = [(50, 40, "Nature Methods | Volume 21 | June 2024 | 1012-1024", 8),
             (50, 52, "https://doi.org/10.1038/s41592-024-02201-0", 8),
             (50, 110, "Single-cell atlas of regulatory syntax in", 20),
             (50, 136, "the developing zebrafish heart", 20),
             (50, 170, "Jane Doe, Wei Chen & Pieter van der Berg", 11),
             (50, 210, "Abstract", 12)]
    y = 228
    for line in _para(LOREM + " " + LOREM):
        items.append((50, y, line, 9)); y += 12
    items.append((50, y + 10, "1 Introduction", 12))
    items.append((50, y + 28, "Earlier atlases (https://doi.org/10.1016/j.cell.2020.01.001) lacked spatial data.", 9))
    _page(doc, items)
    _page(doc, [(50, 60, "Code availability", 12),
                (50, 78, "Code: https://github.com/vanderberglab/zebraheart/tree/main", 9),
                (50, 100, "Data availability", 12),
                (50, 118, "Data are available at https://zenodo.org/records/1234567 .", 9)])
    _page(doc, [(50, 60, "References", 12),
                (50, 78, "1. Smith, J. OtherTool. https://github.com/smithlab/othertool", 9),
                (50, 92, "doi:10.1093/bioinformatics/btz001", 9)])
    doc.set_metadata({"title": "", "author": ""})
    doc.save(d / "doe2024_zebrafish.pdf"); doc.close()

    # B — arXiv preprint: ID only in the rotated left-margin stamp.
    doc = pymupdf.open()
    items = [(30, 600, "arXiv:2401.01234v2  [cs.LG]  5 Jan 2024", 9, 90),
             (80, 110, "Sparse attention for long genomic sequences", 18),
             (80, 140, "Alice Martin    Bob Okafor", 11),
             (80, 180, "Abstract", 12)]
    y = 198
    for line in _para(LOREM, 80):
        items.append((80, y, line, 9)); y += 12
    _page(doc, items)
    doc.save(d / "2401.01234v2.pdf"); doc.close()

    # C — no identifier anywhere → title search.
    doc = pymupdf.open()
    _page(doc, [(50, 100, "Phase separation of transcription factors", 19),
                (50, 125, "in plant immunity", 19),
                (50, 160, "Anna Jansen and Bram de Vries", 11),
                (50, 200, "Plants deploy condensates to tune defence gene expression.", 9)])
    doc.set_metadata({"author": "Anna Jansen; Bram de Vries"})
    doc.save(d / "scan_0042.pdf"); doc.close()

    # D — bioRxiv preprint, DOI with version suffix in header.
    doc = pymupdf.open()
    _page(doc, [(50, 40, "bioRxiv preprint doi: https://doi.org/10.1101/2024.03.15.585123v1", 7),
                (50, 110, "Clonal dynamics of hematopoiesis after transplantation", 18),
                (50, 140, "Sofia Rossi, Mark de Jong", 11),
                (50, 180, "Abstract", 12),
                (50, 198, "Short abstract text that is too short for the PDF abstract fallback.", 9)])
    doc.save(d / "rossi_biorxiv.pdf"); doc.close()

    # E — nothing resolvable.
    doc = pymupdf.open()
    _page(doc, [(50, 100, "Internal lab protocol: buffer preparation notes", 16),
                (50, 140, "Version 3, unpublished", 10)])
    doc.save(d / "protocol.pdf"); doc.close()


# --------------------------------------------------------------------------- API fixtures

CR_A = {"message": {
    "DOI": "10.1038/s41592-024-02201-0", "type": "journal-article",
    "title": ["Single-cell atlas of regulatory syntax in the developing zebrafish heart"],
    "author": [{"given": "Jane", "family": "Doe"}, {"given": "Wei", "family": "Chen"},
               {"given": "Pieter", "family": "van der Berg"}],
    "container-title": ["Nature Methods"], "short-container-title": ["Nat Methods"],
    "publisher": "Springer Science and Business Media LLC",
    "published-print": {"date-parts": [[2024, 6]]}, "published-online": {"date-parts": [[2024, 3, 2]]},
    "volume": "21", "issue": "6", "page": "1012-1024",
    "issn-type": [{"type": "print", "value": "1548-7091"}, {"type": "electronic", "value": "1548-7105"}],
    "license": [{"URL": "https://creativecommons.org/licenses/by/4.0", "content-version": "vor"}],
    "subject": [],
}}
CR_DECOY = {"message": {
    "DOI": "10.1016/j.cell.2020.01.001", "type": "journal-article",
    "title": ["A completely different paper about yeast"],
    "author": [{"given": "Q", "family": "Other"}], "container-title": ["Cell"],
    "issued": {"date-parts": [[2020]]},
}}
OA_A = {"doi": "https://doi.org/10.1038/s41592-024-02201-0",
        "title": "Single-cell atlas of regulatory syntax in the developing zebrafish heart",
        "publication_date": "2024-03-02", "type": "article",
        "authorships": [{"author": {"display_name": "Jane Doe"}}],
        "primary_location": {"source": {"display_name": "Nature Methods", "issn_l": "1548-7091"}},
        "biblio": {"volume": "21", "issue": "6", "first_page": "1012", "last_page": "1024"},
        "open_access": {"is_oa": True, "oa_status": "hybrid",
                        "oa_url": "https://www.nature.com/articles/s41592-024-02201-0.pdf"},
        "best_oa_location": {"license": "cc-by", "version": "publishedVersion"},
        "abstract_inverted_index": {"We": [0], "present": [1], "an": [2], "atlas.": [3]},
        "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/38400001"}}
UP_A = {"is_oa": True, "oa_status": "hybrid",
        "best_oa_location": {"license": "cc-by", "version": "publishedVersion",
                             "url_for_pdf": "https://www.nature.com/articles/s41592-024-02201-0.pdf"}}
EPMC_A = {"resultList": {"result": [{"abstractText": "<h4>Abstract</h4>We present an approach that combines "
                                     "single-cell chromatin accessibility with spatial transcriptomics.",
                                     "pmid": "38400001", "pmcid": "PMC1100001"}]}}

ARXIV_B = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/2401.01234v2</id><published>2024-01-03T10:00:00Z</published>
<title>Sparse attention for long genomic
  sequences</title><summary>  We introduce sparse attention for megabase-scale genomic context. </summary>
<author><name>Alice Martin</name></author><author><name>Bob Okafor</name></author>
<arxiv:doi>10.1093/bioinformatics/btae100</arxiv:doi></entry></feed>"""
CR_B_PUB = {"message": {
    "DOI": "10.1093/bioinformatics/btae100", "type": "journal-article",
    "title": ["Sparse attention for long genomic sequences"],
    "author": [{"given": "Alice", "family": "Martin"}, {"given": "Bob", "family": "Okafor"}],
    "container-title": ["Bioinformatics"], "published-online": {"date-parts": [[2024, 5, 10]]},
    "volume": "40", "issue": "5", "article-number": "btae100", "publisher": "Oxford University Press",
}}
OA_B_PUB = {"doi": "https://doi.org/10.1093/bioinformatics/btae100", "open_access": {"is_oa": True, "oa_status": "gold", "oa_url": "https://academic.oup.com/x.pdf"},
            "best_oa_location": {"license": "cc-by", "version": "publishedVersion"}}

CR_SEARCH_C = {"message": {"items": [
    {"DOI": "10.1000/unrelated", "type": "journal-article", "title": ["Transcription in yeast"],
     "author": [{"given": "X", "family": "Y"}], "issued": {"date-parts": [[2019]]}},
    {"DOI": "10.1105/tpc.23.00001", "type": "journal-article",
     "title": ["Phase separation of transcription factors in plant immunity"],
     "author": [{"given": "Anna", "family": "Jansen"}, {"given": "Bram", "family": "de Vries"}],
     "container-title": ["The Plant Cell"], "published-print": {"date-parts": [[2023, 11]]},
     "volume": "35", "issue": "11", "page": "4001-4019"}]}}
OA_C = {"doi": "https://doi.org/10.1105/tpc.23.00001", "open_access": {"is_oa": False, "oa_status": "closed", "oa_url": None}, "best_oa_location": None}

CR_D = {"message": {
    "DOI": "10.1101/2024.03.15.585123", "type": "posted-content", "subtype": "preprint",
    "title": ["Clonal dynamics of hematopoiesis after transplantation"],
    "author": [{"given": "Sofia", "family": "Rossi"}, {"given": "Mark", "family": "de Jong"}],
    "institution": [{"name": "bioRxiv"}], "posted": {"date-parts": [[2024, 3, 17]]},
    "abstract": "<jats:p>We track clones after transplant.</jats:p>",
}}


def route(url: str, params: dict):
    """Fake Http.get: returns parsed body or None (= 404)."""
    if "api.crossref.org/works/" in url:
        doi = url.split("/works/", 1)[1].lower()
        return {"10.1038/s41592-024-02201-0": CR_A, "10.1016/j.cell.2020.01.001": CR_DECOY,
                "10.1093/bioinformatics/btae100": CR_B_PUB, "10.1101/2024.03.15.585123": CR_D}.get(doi)
    if url.endswith("api.crossref.org/works"):
        q = params.get("query.bibliographic", "").lower()
        return CR_SEARCH_C if "phase separation" in q else {"message": {"items": []}}
    if "api.openalex.org/works/doi:" in url:
        doi = url.split("doi:", 1)[1].lower()
        return {"10.1038/s41592-024-02201-0": OA_A, "10.1105/tpc.23.00001": OA_C,
                "10.1093/bioinformatics/btae100": OA_B_PUB}.get(doi)
    if url.endswith("api.openalex.org/works"):
        return {"results": []}
    if "api.unpaywall.org" in url:
        return UP_A if "s41592" in url else None
    if "export.arxiv.org" in url:
        return ARXIV_B if params.get("id_list") == "2401.01234" else None
    if "europepmc" in url:
        return EPMC_A if "s41592" in params.get("query", "") else None
    return None
