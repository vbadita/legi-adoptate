"""Parser rules, checked on real cdep.ro pages saved in tests/fixtures/."""

from datetime import date
from pathlib import Path

import pytest

from legi import cdep

FIX = Path(__file__).parent / "fixtures"


def proc(name: str) -> cdep.Procedure:
    return cdep.parse_procedure((FIX / name).read_text(encoding="utf-8"), int(name.split("_")[1]))


def test_header_fields():
    p = proc("proc_23416_pending.html")
    assert p.number == "PL-x nr. 499/2026"
    assert p.title.startswith("Proiect de Lege pentru modificarea şi completarea Legii drepturilor")
    assert p.decisional_chamber == "Camera Deputaților"
    assert p.character == "ordinar"
    assert p.senate_number == "L382/2026"
    assert (
        p.promulgation_text_url
        == "https://www.cdep.ro/ords/pls/proiecte/docs?2026/pl499_pr499_26.pdf"
    )


def test_adopted_deposited_not_yet_promulgated():
    p = proc("proc_23416_pending.html")
    [a] = p.adoptions()
    assert (a.day, a.chamber, a.confirmed) == (date(2026, 10, 14), "Camera Deputaților", True)
    assert a.vote == cdep.Vote(262, 0, 0, 1)
    assert [s.chamber for s in a.steps] == ["Senat", "Camera Deputaților"]
    assert p.promulgation() is None


def test_adoptata_wording_after_first_chamber_rejected():
    """'respinsa de catre Senat' then 'adoptata de Camera Deputatilor' (decisive)."""
    p = proc("proc_20798_adoptata.html")
    [a] = p.adoptions()
    assert (a.day, a.chamber) == (date(2026, 6, 30), "Camera Deputaților")
    assert a.vote == cdep.Vote(276, 1, 0, None)
    prom = p.promulgation()
    assert (prom.day, prom.decree, prom.law) == (date(2026, 7, 20), "431/2026", "156/2026")


def test_last_vote_before_deposit_is_definitive():
    """Decisive chamber (Camera) votes 29.07, the Senate votes its articles 30.07,
    deposit 31.07: Parliament adopted the law on 30.07. The public vote is the
    Camera's (the Senate's counts are not on this site)."""
    p = proc("proc_21012_last_vote_senate.html")
    [a] = p.adoptions()
    assert (a.day, a.chamber, a.confirmed) == (date(2026, 7, 30), "Senat", True)
    main = p.main_vote(a)
    assert (main.day, main.chamber, main.vote) == (
        date(2026, 7, 29),
        "Camera Deputaților",
        cdep.Vote(236, 4, 51, 2),
    )
    assert p.promulgation().law == "169/2026"


def test_reexamination_gives_two_rounds():
    p = proc("proc_21222_reexamination.html")
    rounds = p.adoptions()
    assert [(a.round, a.day) for a in rounds] == [(1, date(2023, 11, 14)), (2, date(2025, 10, 15))]
    assert rounds[1].vote == cdep.Vote(295, 0, 0, None)
    kinds = [e.kind for e in p.events]
    assert "reexamination_requested" in kinds and "ccr_decision" in kinds
    assert p.promulgation().law == "160/2026"


def test_senate_decides():
    p = proc("proc_22931_senate_decides.html")
    assert p.decisional_chamber == "Senat"
    [a] = p.adoptions()
    assert (a.day, a.chamber, a.vote) == (date(2026, 5, 4), "Senat", None)
    assert p.main_vote(a).vote == cdep.Vote(220, 7, 9, 53)  # the Chamber's earlier vote
    assert p.promulgation().law == "87/2026"
    assert p.promulgation_text_url is None  # adopted text lives on senat.ro


def _procedure(events, decisional="Camera Deputaților"):
    return cdep.Procedure(1, "", "", decisional, None, None, None, None, events)


def _ev(day, text):
    return cdep.Event(day=day, text=text, **cdep.classify(text))


def test_open_round_counts_only_the_decisive_chamber():
    first_chamber_only = _procedure([_ev(date(2026, 10, 20), "adoptat de Senat")])
    assert first_chamber_only.adoptions() == []
    voted_today = _procedure(
        [
            _ev(date(2026, 10, 20), "adoptat de Senat"),
            _ev(
                date(2026, 10, 24),
                "adoptat de Camera Deputatilor rezultat vot pentru=200, contra=10, abtineri=5",
            ),
        ]
    )
    [a] = voted_today.adoptions()
    assert (a.day, a.confirmed, a.vote) == (date(2026, 10, 24), False, cdep.Vote(200, 10, 5, None))


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("adoptat de Senat (ca urmare a cererii de reexaminare)", "adopted"),
        ("adoptată de Camera Deputaților", "adopted"),
        ("respinsa de catre Senat", "rejected"),
        ("depunere la Secretarul general pentru exercitarea dreptului de sesizare", "deposited"),
        ("trimitere la Presedintele României pentru promulgare", "sent_to_president"),
        ("Presedintele României solicita reexaminarea;cererea ...", "reexamination_requested"),
        ("promulgata prin Decret nr. 303/2026 devine Legea nr. 87/2026", "promulgated"),
        ("sesizare de neconstituţionalitate (formulată de Președintele României)", "ccr_referral"),
        ("Curtea Constitutionala decide: ( D.C.C. nr.391/2026 )", "ccr_decision"),
        ("primire raport favorabil de la: Comisia ...", "other"),
    ],
)
def test_classify(text, kind):
    assert cdep.classify(text)["kind"] == kind


def test_list_became_laws_2026():
    entries = cdep.parse_list((FIX / "list_anl_2026.html").read_text(encoding="utf-8"))
    assert len(entries) == 204
    assert entries[1].idp == 22591
    assert entries[1].title == "Proiect de Lege privind plata pensiilor private"
    assert entries[1].extra == "Lege 2/2026 05.01.2026"


def test_list_just_adopted():
    entries = cdep.parse_list((FIX / "list_std_G.html").read_text(encoding="utf-8"))
    assert entries[0].idp == 21264 and entries[0].extra == "20.10.2026"
    assert any(e.idp == 23416 for e in entries)


def test_acts_list_and_act_page():
    acts = cdep.parse_acts_list((FIX / "acts_laws_2026.html").read_text(encoding="utf-8"))
    lege2 = next(a for a in acts if a.law == "2/2026")
    assert (lege2.day, lege2.ida) == (date(2026, 1, 5), 218751)
    assert lege2.title == "LEGE privind plata pensiilor private"
    info = cdep.parse_act((FIX / "act_218751_lege_2_2026.html").read_text(encoding="utf-8"))
    assert (info.mo_number, info.mo_day, info.bill_ref) == (
        "2/2026",
        date(2026, 1, 5),
        "P.L. nr. 320/2025",
    )


def test_ordinance_approval_wording():
    """'proiectul de lege pentru aprobarea ordonantei de urgenta adoptat de Camera
    Deputatilor': definitive vote of 16.09.2026, 271-0-2, as in the official vote XML."""
    p = proc("proc_23297_ordinance_approval.html")
    assert p.number == "PL-x nr. 428/2026"
    [a] = p.adoptions()
    assert (a.day, a.chamber, a.vote) == (
        date(2026, 9, 16),
        "Camera Deputaților",
        cdep.Vote(271, 0, 2, None),
    )


def test_document_label_is_not_a_vote():
    assert (
        cdep.classify("înregistrat la Camera Deputatilor Forma adoptată de Senat")["kind"]
        == "other"
    )


def test_captcha_page_is_rejected():
    captcha = "<html><body>Firewall Captcha Authentication Security check</body></html>"
    with pytest.raises(cdep.NotAProcedurePage):
        cdep.parse_procedure(captcha, 1)
