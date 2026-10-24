"""Plain-language explanations: text extraction, checks, retries, sources."""

import io
import json
import re
import urllib.error
from pathlib import Path

import pytest

from legi import cli, explain, sources
from legi.text import is_scanned, page_images, pdf_to_text

FIX = Path(__file__).parent / "fixtures"


# ---- text extraction (real PDFs) ----------------------------------------------------------


def test_superscript_articles_are_kept():
    text = pdf_to_text((FIX / "pl499_forma_promulgare.pdf").read_bytes())
    assert "art. 27^1" in text and "28^2" in text
    assert "art. 271" not in text  # the wrong number plain extraction produces


def test_margin_titles_do_not_cut_sentences():
    flat = re.sub(r"\s+", " ", pdf_to_text((FIX / "pl489_codul_penal_margin.pdf").read_bytes()))
    assert (
        "(1) Efectuarea, în orice mod, de operațiuni de procurare, deținere, stocare, distribuire"
        in flat
    )
    assert "Articolul 374 va avea următorul cuprins" in flat  # a full-width line stays whole
    assert "[Titlu marginal: „ Operațiuni cu materiale ce conțin abuzuri sexuale" in flat


def test_scan_detection_and_images():
    scan = (FIX / "senat_L220_LP_scan.pdf").read_bytes()
    assert is_scanned(scan) and not is_scanned((FIX / "pl499_forma_promulgare.pdf").read_bytes())
    images = page_images(scan)
    assert len(images) == 3 and all(img[:2] == b"\xff\xd8" for img in images)  # JPEG


# ---- checks -------------------------------------------------------------------------------

LAW = (
    "(1) Deținerea de materiale se pedepsește cu închisoarea de la 2 la 7 ani. "
    "(5) Vizionarea transmisiunilor în direct se pedepsește cu închisoarea de la 1 la 5 ani. "
    "Contractul-cadru se corelează cu cel puțin 60 de zile înainte."
)


def answer(changes, **over):
    out = {
        "titlu_simplu": "Pedepse pentru materiale cu abuzuri asupra minorilor",
        "pe_scurt": "Legea stabilește pedepse.",
        "ce_se_schimba": changes,
        "cine_este_afectat": ["Instanțele"],
        "de_cand": None,
        "tip": "modificarea unor legi",
    }
    return {**out, **over}


def test_good_answer_passes():
    a = answer(
        [
            {
                "text": "Deținerea: închisoare de la 2 la 7 ani.",
                "citat": "Deținerea de materiale se pedepsește cu închisoarea de la 2 la 7 ani.",
            }
        ]
    )
    assert explain.validate(a) == []
    assert explain.citation_problems(a, LAW) == []
    assert explain.unsupported_numbers(a, LAW) == []


def test_penalty_on_the_wrong_offence_is_caught():
    """The real v1 failure: every number exists in the law, but 2-7 years is the
    penalty for holding, not for watching live broadcasts."""
    a = answer(
        [
            {
                "text": "Vizionarea în direct: de la 2 la 7 ani.",
                "citat": (
                    "Vizionarea transmisiunilor în direct se pedepsește cu închisoarea "
                    "de la 1 la 5 ani."
                ),
            }
        ]
    )
    assert explain.citation_problems(a, LAW) == [
        "punctul 1: numerele 2, 7 nu apar în citatul punctului"
    ]


def test_invented_quote_is_caught():
    a = answer([{"text": "Ceva", "citat": "Acest text nu există în lege."}])
    assert explain.citation_problems(a, LAW) == ["punctul 1: citatul nu apare exact în text"]


def test_quote_matching_ignores_line_breaks_and_cedilla():
    a = answer([{"text": "60 de zile", "citat": "cu cel puţin\n60 de zile"}])  # ţ cedilla, newline
    assert explain.citation_problems(a, LAW) == []


def test_invented_number_in_summary_is_caught():
    a = answer([{"text": "x", "citat": "Deținerea de materiale"}], pe_scurt="Se aplică din 2027.")
    assert explain.unsupported_numbers(a, LAW) == ["2027"]


@pytest.mark.parametrize(
    "over",
    [
        {"tip": "opinie"},
        {"titlu_simplu": "x" * 200},
        {"ce_se_schimba": []},
        {"cine_este_afectat": ["a", "b", "c", "d", "e"]},
    ],
)
def test_structure_violations(over):
    a = answer([{"text": "t", "citat": "c"}], **over)
    assert explain.validate(a) != []


# ---- retries and errors ----------------------------------------------------------------------


def test_rejected_answer_is_retried_with_the_reason():
    calls = []
    bad = answer([{"text": "de la 2 la 7 ani", "citat": "inventat"}])
    good = answer(
        [{"text": "de la 2 la 7 ani", "citat": "se pedepsește cu închisoarea de la 2 la 7 ani"}]
    )

    def fake_call(parts, key, model):
        calls.append(parts)
        return bad if len(calls) == 1 else good

    r = explain.explain(text=LAW, title="T", api_key="k", model="m", call=fake_call)
    assert r["attempts"] == 2 and r["checked_against_text"] is True
    assert "citatul nu apare exact" in calls[1][-1]["text"]  # the model is told why


def test_two_bad_answers_mean_no_explanation():
    bad = answer([{"text": "x", "citat": "inventat"}])
    with pytest.raises(explain.ExplainError, match="rejected after 2 attempts"):
        explain.explain(text=LAW, title="T", api_key="k", model="m", call=lambda *a: bad)


def test_quota_exhausted_is_distinct_and_key_never_in_errors():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "x", {}, io.BytesIO())

    with pytest.raises(explain.QuotaExhausted):
        explain.call_gemini([{"text": "x"}], "SECRET-KEY", "m", opener=opener)

    def opener500(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "x", {}, io.BytesIO())

    with pytest.raises(explain.ExplainError) as exc:
        explain.call_gemini([{"text": "x"}], "SECRET-KEY", "m", opener=opener500)
    assert "SECRET-KEY" not in str(exc.value)


def test_request_uses_schema_and_system_prompt():
    seen = {}

    def opener(req, timeout):
        seen.update(json.loads(req.data))
        body = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
        return io.BytesIO(json.dumps(body).encode())

    explain.call_gemini([{"text": "x"}], "k", "m", opener=opener)
    assert seen["generationConfig"]["responseJsonSchema"] == explain.SCHEMA
    assert "Folosește DOAR ce scrie în text" in seen["systemInstruction"]["parts"][0]["text"]


# ---- sources and scheduling --------------------------------------------------------------------


def test_text_sources_order():
    rec = {"text_pdf": "https://www.cdep.ro/x.pdf", "senate": {"number": "L220/2026", "url": "u"}}
    # a Chamber text exists: only that one (never a scan because cdep.ro was down)
    assert [u for _, u in sources.text_sources(rec)] == ["https://www.cdep.ro/x.pdf"]
    senate_only = {"text_pdf": None, "senate": {"number": "L220/2026", "url": "u"}}
    assert [u for _, u in sources.text_sources(senate_only)] == [
        "https://www.senat.ro/legis/PDF/2026/26L220LP.PDF",
        "https://www.senat.ro/legis/PDF/2026/26L220FS.PDF",
    ]


def test_needs_explanation():
    rec = {"rounds": [{"round": 2}]}
    assert cli.needs_explanation(rec, None)
    assert not cli.needs_explanation(rec, {"prompt_version": explain.PROMPT_VERSION, "round": 2})
    assert cli.needs_explanation(rec, {"prompt_version": explain.PROMPT_VERSION - 1, "round": 2})
    assert cli.needs_explanation(rec, {"prompt_version": explain.PROMPT_VERSION, "round": 1})
    assert not cli.needs_explanation({"rounds": []}, None)


def test_elided_quote_pieces_must_be_verbatim_and_in_order():
    ok = answer(
        [
            {
                "text": "de la 2 la 7 ani; de la 1 la 5 ani",
                "citat": "Deținerea de materiale se pedepsește ... cu închisoarea de la 2 la 7 ani. (5) Vizionarea transmisiunilor ... de la 1 la 5 ani.",
            }
        ]
    )
    assert explain.citation_problems(ok, LAW) == []
    reordered = answer(
        [
            {
                "text": "x",
                "citat": "Vizionarea transmisiunilor în direct ... Deținerea de materiale se pedepsește",
            }
        ]
    )
    assert explain.citation_problems(reordered, LAW) == [
        "punctul 1: citatul nu apare exact în text"
    ]
    invented_piece = answer(
        [
            {
                "text": "x",
                "citat": "Deținerea de materiale ... se confiscă automobilul proprietarului",
            }
        ]
    )
    assert explain.citation_problems(invented_piece, LAW) == [
        "punctul 1: citatul nu apare exact în text"
    ]


def test_elided_pieces_must_come_from_one_passage():
    far = "Primul alineat spune ceva important. " + "x " * 1500 + "Ultimul alineat spune altceva."
    a = answer(
        [
            {
                "text": "x",
                "citat": "Primul alineat spune ceva important ... Ultimul alineat spune altceva.",
            }
        ]
    )
    assert explain.citation_problems(a, far) == ["punctul 1: citatul nu apare exact în text"]


def test_rejected_laws_are_retried_on_later_runs_up_to_3():
    rec = {"rounds": [{"round": 1}]}
    base = {"prompt_version": explain.PROMPT_VERSION, "round": 1, "status": "rejected"}
    assert cli.needs_explanation(rec, {**base, "tries": 1})
    assert cli.needs_explanation(rec, {**base, "tries": 2})
    assert not cli.needs_explanation(rec, {**base, "tries": 3})


def test_hyphen_line_break_matches():
    a = answer([{"text": "x", "citat": "printr-un sistem informatic"}])
    assert explain.citation_problems(a, "transmise printr- un sistem informatic sau") == []


def test_explanation_of_an_outdated_text_is_regenerated():
    rec = {"rounds": [{"round": 2}], "text_pdf": "https://www.cdep.ro/new.pdf", "senate": None}
    old = {
        "prompt_version": explain.PROMPT_VERSION,
        "round": 2,
        "status": "ok",
        "source": {"url": "https://www.cdep.ro/old.pdf"},
    }
    assert cli.needs_explanation(rec, old)
    assert not cli.needs_explanation(rec, {**old, "source": {"url": "https://www.cdep.ro/new.pdf"}})
