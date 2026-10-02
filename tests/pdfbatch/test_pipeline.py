from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from pdfbatch import apis, pdf_reader
from pdfbatch.identifiers import clean_doi, find_candidates
from pdfbatch.links import classify
from pdfbatch.pdf_reader import read_pdf
from pdfbatch.pipeline import Settings, process_pdf
from pdfbatch.record import Author, Record
from pdfbatch.writers import apa, ris_entry, write_markdown

from . import fixtures


@pytest.fixture(scope="module")
def pdf_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("pdfs")
    fixtures.make_pdfs(d)
    return d


@pytest.fixture
def http(monkeypatch):
    h = apis.Http("test@example.org", cache_dir=None)
    calls = []

    def fake_get(self, url, params=None, *, as_json=True, min_interval=0, retries=0):
        calls.append(url)
        return fixtures.route(url, params or {})

    monkeypatch.setattr(apis.Http, "get", fake_get)
    h.calls = calls
    return h


def settings(**kw):
    return Settings(**kw)


# --------------------------------------------------------------------------- unit

@pytest.mark.parametrize("raw, want", [
    ("https://doi.org/10.1038/s41592-024-02201-0.", "10.1038/s41592-024-02201-0"),
    ("doi:10.1101/2024.03.15.585123v1", "10.1101/2024.03.15.585123"),
    ("10.1016/S0092-8674(20)30001-1)", "10.1016/s0092-8674(20)30001-1"),
    ("10.1371/journal.pone.0123456/full", "10.1371/journal.pone.0123456"),
    ("not a doi", None),
])
def test_clean_doi(raw, want):
    assert clean_doi(raw) == want


def test_author_split():
    a = Author.from_display("Pieter van der Berg")
    assert (a.family, a.given) == ("van der Berg", "Pieter")
    assert Author.from_display("Doe, Jane").ris() == "Doe, Jane"


def test_apa_authors():
    r = Record(authors=[Author("Doe", "Jane Q."), Author(literal="Human Cell Atlas Consortium")], year="2024")
    assert apa(r) == "Doe, J. Q., & Human Cell Atlas Consortium (2024)."


@pytest.mark.parametrize("first, last, want", [
    ("Article5", "Article5", (None, None, "5")),         # OpenAlex's way of giving an article number
    ("e1001", "e1001", ("e1001", None, None)),
    ("1012", "1024", ("1012", "1024", None)),
])
def test_openalex_pages(first, last, want):
    f = apis.openalex_to_fields({"biblio": {"first_page": first, "last_page": last}})
    assert (f["first_page"], f["last_page"], f["article_number"]) == want


def test_apa_article_number():
    r = Record(title="T", year="2003", journal="J", volume="2", issue="1", article_number="5")
    assert apa(r) == "T. (2003). *J*, *2*(1), Article 5."


@pytest.mark.parametrize("url, want", [
    ("https://github.com/Org/Repo/tree/main/src?x=1", "https://github.com/Org/Repo"),
    ("https://gitlab.com/a/b/c/repo/-/issues", "https://gitlab.com/a/b/c/repo"),
    ("https://hf.co/datasets/org/set", "https://huggingface.co/datasets/org/set"),
    ("https://zenodo.org/record/42", "https://doi.org/10.5281/zenodo.42"),
    ("https://github.com/features/actions", None),          # site page, not a repo
    ("https://example.org/a/b", None),
])
def test_classify(url, want):
    f = classify(url)
    assert (f.url if f else None) == want


def test_header_doi_outranks_cited_doi(pdf_dir):
    c = find_candidates(read_pdf(pdf_dir / "doe2024_zebrafish.pdf"))
    assert c[0].value == "10.1038/s41592-024-02201-0"
    assert c[0].source == "page1-header"


def test_title_guess(pdf_dir):
    p = read_pdf(pdf_dir / "scan_0042.pdf")
    assert p.title_guess == "Phase separation of transcription factors in plant immunity"


# --------------------------------------------------------------------------- end to end

def test_journal_article(pdf_dir, http):
    r = process_pdf(pdf_dir / "doe2024_zebrafish.pdf", http, settings(unpaywall_email="t@example.org"))
    assert r.confidence == "high"
    assert r.doi == "10.1038/s41592-024-02201-0"
    assert r.resolved_via[:2] == ["doi:page1-header", "crossref"]
    assert [a.family for a in r.authors] == ["Doe", "Chen", "van der Berg"]
    assert (r.journal, r.volume, r.issue, r.first_page, r.last_page) == ("Nature Methods", "21", "6", "1012", "1024")
    assert r.oa.status == "hybrid" and r.oa.license == "cc-by" and r.oa.source == "unpaywall"
    assert r.pmid == "38400001"
    assert r.abstract_source == "europepmc"
    urls = {l.url: l for l in r.links}
    assert "https://github.com/vanderberglab/zebraheart" in urls
    assert "https://doi.org/10.5281/zenodo.1234567" in urls
    assert "https://github.com/smithlab/othertool" not in urls     # cited in References
    assert urls["https://doi.org/10.5281/zenodo.1234567"].kind == "data"
    assert urls["https://doi.org/10.5281/zenodo.1234567"].label is None
    # The decoy DOI on page 1 was never needed.
    assert not any("j.cell.2020" in c for c in http.calls)


def test_arxiv_held_version(pdf_dir, http):
    r = process_pdf(pdf_dir / "2401.01234v2.pdf", http, settings())
    assert r.arxiv_id == "2401.01234" and r.is_preprint
    assert r.oa.status == "green"
    assert any("btae100" in n for n in r.notes)


def test_arxiv_prefer_published(pdf_dir, http):
    r = process_pdf(pdf_dir / "2401.01234v2.pdf", http, settings(prefer="published"))
    assert r.doi == "10.1093/bioinformatics/btae100"
    assert r.journal == "Bioinformatics" and r.article_number == "btae100"
    assert not r.is_preprint and r.oa.status == "gold"
    assert r.related_doi == "10.48550/arxiv.2401.01234"


def test_title_search_without_doi(pdf_dir, http):
    r = process_pdf(pdf_dir / "scan_0042.pdf", http, settings())
    assert r.doi == "10.1105/tpc.23.00001"
    assert r.confidence == "high" and r.resolved_via[0] == "title-search"
    assert r.oa.status == "closed" and r.oa.is_oa is False


def test_title_below_a_larger_banner(tmp_path, http):
    # Working papers often set the series name larger than the title.
    doc = pymupdf.open()
    fixtures._page(doc, [(50, 60, "Working Paper Series in Plant Biology", 24),
                         (50, 110, "Phase separation of transcription factors", 19),
                         (50, 135, "in plant immunity", 19),
                         (50, 170, "Anna Jansen and Bram de Vries", 11)])
    doc.save(tmp_path / "working_paper.pdf"); doc.close()
    r = process_pdf(tmp_path / "working_paper.pdf", http, settings())
    assert r.doi == "10.1105/tpc.23.00001" and r.confidence == "high"


@pytest.fixture
def scan(tmp_path):
    """An image-only PDF of a title page, plus the text page it was rendered from."""
    text = pymupdf.open()
    fixtures._page(text, [(50, 100, "Phase separation of transcription factors", 19),
                          (50, 125, "in plant immunity", 19),
                          (50, 160, "Anna Jansen and Bram de Vries", 11)])
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_image(pymupdf.Rect(0, 0, 595, 842),
                                                      pixmap=text[0].get_pixmap(dpi=100))
    doc.save(tmp_path / "scan.pdf"); doc.close()
    yield tmp_path / "scan.pdf", text[0]
    text.close()


def test_scan_is_read_by_ocr(scan, http, monkeypatch):
    path, text_page = scan        # stands in for Tesseract, which tests cannot assume
    monkeypatch.setattr(pdf_reader, "_ocr", lambda page: text_page.get_textpage(flags=pymupdf.TEXTFLAGS_TEXT))
    r = process_pdf(path, http, settings())
    assert r.doi == "10.1105/tpc.23.00001" and r.confidence == "high"
    assert "Scanned PDF: page 1 was read by OCR." in r.notes


def test_tessdata_outside_path(tmp_path, monkeypatch):
    # The Windows installer does not put Tesseract on PATH; look in its standard folders.
    def not_on_path():
        raise RuntimeError("No tessdata specified and Tesseract is not installed")
    (tmp_path / "eng.traineddata").touch()
    monkeypatch.setattr(pymupdf, "get_tessdata", not_on_path)
    monkeypatch.setattr(pdf_reader, "_TESSDATA_DIRS", (str(tmp_path / "missing"), str(tmp_path)))
    pdf_reader._tessdata.cache_clear()
    try:
        assert pdf_reader._tessdata() == str(tmp_path)
    finally:
        pdf_reader._tessdata.cache_clear()


def test_scan_without_tesseract(scan, http, monkeypatch):
    monkeypatch.setattr(pdf_reader, "_ocr", lambda page: None)
    r = process_pdf(scan[0], http, settings())
    assert r.confidence == "unresolved" and any("install Tesseract" in n for n in r.notes)


def test_all_caps_title_page(tmp_path, http):
    # Theses set the title in capitals, below a smaller university header.
    doc = pymupdf.open()
    fixtures._page(doc, [(50, 60, "KATHOLIEKE UNIVERSITEIT LEUVEN", 8),
                         (50, 70, "NATURE GENETICS", 14),
                         (50, 90, "A R T I C L E S", 13),
                         (50, 290, "PROBABILISTIC METHODS TO SEARCH", 12),
                         (50, 304, "FOR REGULATORY ELEMENTS", 12)])
    doc.save(tmp_path / "thesis.pdf"); doc.close()
    assert read_pdf(tmp_path / "thesis.pdf").title_guesses[0] == \
        "PROBABILISTIC METHODS TO SEARCH FOR REGULATORY ELEMENTS"
    r = process_pdf(tmp_path / "thesis.pdf", http, settings())
    assert r.confidence == "unresolved"
    assert r.title == "Probabilistic methods to search for regulatory elements"


def test_title_search_falls_back_to_openalex(pdf_dir, http, monkeypatch):
    # CrossRef's top hits can all be records *about* the paper, which fail validation.
    real = apis.crossref_to_fields(fixtures.CR_SEARCH_C["message"]["items"][1])
    review = dict(real, doi="10.3410/f.1002148.52456", authors=[Author("Reviewer", "R")],
                  title="Faculty Opinions recommendation of " + real["title"])
    monkeypatch.setattr(apis, "crossref_search", lambda *a, **k: [review])
    monkeypatch.setattr(apis, "openalex_search", lambda *a, **k: [dict(real)])
    r = process_pdf(pdf_dir / "scan_0042.pdf", http, settings())
    assert r.doi == "10.1105/tpc.23.00001" and r.confidence == "high"
    assert r.resolved_via[:2] == ["title-search", "openalex-search"]


def test_biorxiv_rule_oa(pdf_dir, http):
    r = process_pdf(pdf_dir / "rossi_biorxiv.pdf", http, settings())
    assert r.doi == "10.1101/2024.03.15.585123"
    assert r.is_preprint and r.journal == "bioRxiv"
    assert r.oa.status == "green" and r.oa.source == "rule"
    assert r.abstract == "We track clones after transplant."


def test_unresolved(pdf_dir, http):
    r = process_pdf(pdf_dir / "protocol.pdf", http, settings())
    assert r.confidence == "unresolved"
    assert r.title.startswith("Internal lab protocol")


def test_ris_and_markdown(pdf_dir, http, tmp_path):
    recs = [process_pdf(p, http, settings()) for p in sorted(pdf_dir.glob("*.pdf"))]
    ris = "".join(ris_entry(r) for r in recs)
    assert ris.count("TY  - ") == ris.count("ER  - ") == 5
    assert "AU  - van der Berg, Pieter\r\n" in ris
    assert "SP  - 1012\r\nEP  - 1024" in ris
    assert "KW  - OA: hybrid" in ris and "KW  - pdfbatch: needs review" in ris
    assert "L1  - file:///" in ris
    for line in ris.split("\r\n"):
        assert line == "" or line[2:6] == "  - ", line
    md = tmp_path / "papers.md"
    write_markdown(recs, md)
    text = md.read_text(encoding="utf-8")
    assert "## Needs review" in text and "protocol.pdf" in text
    assert "## Summary" not in text and "**Authors:**" not in text and "### " not in text
    assert text.split("## Papers\n\n", 1)[1].startswith("**File:** [`")
    assert "**Open access:** hybrid" in text and "🟠" not in text
    assert "Abstract" not in text
    assert "*Nature Methods*, *21*(6), 1012–1024." in text
