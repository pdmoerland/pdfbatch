from __future__ import annotations

from pathlib import Path

import pytest

from pdfbatch import apis
from pdfbatch.identifiers import clean_doi, find_candidates
from pdfbatch.pdf_reader import read_pdf
from pdfbatch.pipeline import Settings, process_pdf
from pdfbatch.record import Author
from pdfbatch.writers import ris_entry, write_markdown

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
    return Settings(mailto="test@example.org", **kw)


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
