"""The 3 status columns of a law, in words a citizen understands. Pure functions."""

from __future__ import annotations

from datetime import date

MONTHS = [
    "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
    "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie",
]  # fmt: skip
WEEKDAYS = ["luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică"]


def ro_date(iso: str | date) -> str:
    """'2026-10-14' -> '14 octombrie 2026'."""
    d = date.fromisoformat(iso) if isinstance(iso, str) else iso
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def ro_weekday(iso: str | date) -> str:
    d = date.fromisoformat(iso) if isinstance(iso, str) else iso
    return WEEKDAYS[d.weekday()]


# What happens after the final vote, newest event decides (column 2 while pending).
WAITING = {
    "deposited": (
        "La Secretarul general: câteva zile în care legea poate fi contestată "
        "la Curtea Constituțională"
    ),
    "sent_to_president": "Trimisă Președintelui pentru promulgare",
    "ccr_referral": "Contestată la Curtea Constituțională",
    "ccr_decision": "Curtea Constituțională a decis; urmează pașii următori",
    "reexamination_requested": "Retrimisă Parlamentului de Președinte, pentru reexaminare",
}


def vote_text(vote: dict | None) -> str | None:
    if not vote:
        return None
    where = "Camera Deputaților" if vote["chamber"].startswith("Camera") else "Senat"
    return (
        f"Vot în {where}: {vote['pentru']} pentru, {vote['contra']} împotrivă, "
        f"{vote['abtineri']} {'abținere' if vote['abtineri'] == 1 else 'abțineri'}"
    )


def columns(rec: dict) -> dict:
    """{'adopted': {...}, 'promulgated': {...}, 'published': {...}}; each has
    state = done | waiting | none, a short label and an optional detail."""
    r = rec["rounds"][-1]
    adopted = {
        "state": "done" if r["confirmed"] else "waiting",
        "label": ro_date(r["adopted_on"]),
        "detail": (
            f"Votul final: {'Camera Deputaților' if r['chamber'].startswith('Camera') else 'Senat'}"
            + ("" if r["confirmed"] else " · urmează confirmarea (depunerea legii)")
        ),
        "vote": vote_text(r["public_vote"]),
        "round": r["round"],
    }
    law, prom = rec.get("law"), rec.get("promulgation")
    if law:
        promulgated = {
            "state": "done",
            "label": f"Legea nr. {law['number']}",
            "detail": f"din {ro_date(law['day'])}",
        }
    elif prom and prom["day"] >= r["adopted_on"]:
        promulgated = {
            "state": "done",
            "label": f"Decretul nr. {prom['decree']}",
            "detail": f"din {ro_date(prom['day'])}; numărul legii urmează",
        }
    else:
        after = [
            e
            for e in rec.get("timeline", [])
            if e["day"] >= r["adopted_on"] and e["kind"] in WAITING
        ]
        promulgated = {
            "state": "waiting",
            "label": "În așteptare",
            "detail": WAITING[after[-1]["kind"]] if after else "Votată; urmează depunerea legii",
        }
    mo = rec.get("monitor")
    if mo:
        published = {
            "state": "done",
            "label": f"M.Of. nr. {mo['number'].split('/')[0]}",
            "detail": f"din {ro_date(mo['day'])}"
            + (" · verificat în Monitorul Oficial" if mo.get("verified") else ""),
        }
    elif promulgated["state"] == "done":
        published = {"state": "waiting", "label": "În curs de publicare", "detail": None}
    else:
        published = {"state": "none", "label": "—", "detail": "după promulgare"}
    return {"adopted": adopted, "promulgated": promulgated, "published": published}
