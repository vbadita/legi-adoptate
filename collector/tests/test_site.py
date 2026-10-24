"""Static site: built from 3 real law records (one explained)."""

import json
import re
import shutil
from datetime import date
from pathlib import Path

import pytest

from legi import site, status

FIX = Path(__file__).parent / "fixtures" / "site_data"
TODAY = date(2026, 10, 24)


@pytest.fixture
def built(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIX, data)
    out = tmp_path / "out"
    stats = site.build(data, out, today=TODAY)
    return data, out, stats


def page(out, rel):
    return (out / rel).read_text(encoding="utf-8")


def test_every_day_has_a_page(built):
    _, out, stats = built
    assert stats["day_pages"] == (TODAY - site.COVERAGE_START).days + 1
    assert (out / "zi/2026-01-01.html").exists() and (out / "zi/2026-10-24.html").exists()
    assert "nu a adoptat definitiv nicio lege" in page(out, "zi/2026-10-24.html")
    assert 'name="robots" content="noindex"' in page(out, "zi/2026-10-24.html")  # empty days


def test_day_page_lists_the_laws_of_that_day(built):
    _, out, _ = built
    day = page(out, "zi/2026-10-14.html")
    assert "Modificări privind creșterea porcinelor" in day  # explained law: plain title
    assert "252 pentru, 0 împotrivă, 5 abțineri" in day
    assert "La Secretarul general" in day  # column 2 says why it is still waiting


def test_three_columns_for_a_published_law(built):
    _, out, _ = built
    law = page(out, "lege/22703.html")
    assert "Legea nr. 188/2026" in law and "M.Of. nr. 820" in law
    assert "Explicația pe înțelesul tuturor nu este încă disponibilă" in law


def test_law_page_shows_quotes_and_ai_label(built):
    _, out, _ = built
    law = page(out, "lege/23415.html")
    assert "Din textul legii:" in law and "10.000 lei până la 15.000 lei" in law
    assert "generată automat" in law and "verificat automat" in law


def test_everything_is_escaped(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIX, data)
    rec_path = data / "laws" / "22703.json"
    rec = json.loads(rec_path.read_text())
    rec["title"] = '<script>alert("x")</script> Lege'
    rec_path.write_text(json.dumps(rec))
    out = tmp_path / "out"
    site.build(data, out, today=TODAY)
    law = page(out, "lege/22703.html")
    assert "<script>alert" not in law and "&lt;script&gt;alert" in law


def test_no_inline_code_anywhere(built):
    _, out, _ = built
    for f in out.rglob("*.html"):
        assert not re.search(r"<script|<style|\sstyle=|\son\w+=", f.read_text()), f


def test_internal_links_resolve(built):
    _, out, _ = built
    for f in out.rglob("*.html"):
        for href in re.findall(r'(?:href|action)="(/legi-adoptate/[^"#?]*)', f.read_text()):
            rel = href[len("/legi-adoptate/") :]
            assert (out / (rel or "index.html")).exists(), (f, href)


def test_rebuild_swaps_atomically_and_leaves_no_temp_dirs(built):
    data, out, _ = built
    site.build(data, out, today=TODAY)
    leftovers = [p.name for p in out.parent.iterdir() if p.name.startswith((".site-", "out.old"))]
    assert leftovers == []


def test_feed_and_sitemap(built):
    _, out, _ = built
    feed = page(out, "feed.xml")
    rounds = sum(len(json.loads(f.read_text())["rounds"]) for f in (FIX / "laws").glob("*.json"))
    assert feed.count("<entry>") == rounds  # one entry per adoption, not per law
    assert "Modificări privind creșterea porcinelor" in feed
    assert "https://apps.badita.com/legi-adoptate/lege/23415.html" in page(out, "sitemap.xml")


def test_vote_grammar():
    v = {"chamber": "Camera Deputaților", "pentru": 262, "contra": 5, "abtineri": 1}
    assert status.vote_text(v) == "Vot în Camera Deputaților: 262 pentru, 5 împotrivă, 1 abținere"


def test_old_stale_bills_are_out_of_coverage():
    stale = {
        "rounds": [{"adopted_on": "2005-04-07"}],
        "promulgation": None,
        "law": None,
        "monitor": None,
    }
    late_law = {
        "rounds": [{"adopted_on": "2025-10-15"}],
        "law": {"day": "2026-07-23"},
        "promulgation": None,
        "monitor": None,
    }
    assert not site.in_coverage(stale) and site.in_coverage(late_law)
