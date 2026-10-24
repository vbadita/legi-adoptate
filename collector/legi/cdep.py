"""Parsers for the Chamber of Deputies site (www.cdep.ro, paths under /ords/).

Pure functions: HTML text in, dataclasses out. No network here, so every rule
is tested on real saved pages (tests/fixtures).

A law goes through dated events (procedure page, "Derularea procedurii"):
  adoptat de Senat / adoptat de Camera Deputatilor   (one or more rounds)
  depunere la Secretarul general ...                 (right after Parliament's DEFINITIVE vote)
  trimitere la Presedintele Romaniei pentru promulgare
  promulgata prin Decret nr. X/Y [devine Legea nr. N/Y]
Monitorul Oficial is not on this page: see parse_act().
"""

from __future__ import annotations

import html as htmllib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urljoin

BASE = "https://www.cdep.ro"
PROCEDURE_URL = BASE + "/ords/pls/proiecte/upl_pck2015.proiect?cam=2&idp={idp}"

DATE_RE = r"\d\d\.\d\d\.\d{4}"
VOTE_RE = re.compile(r"pentru=(\d+), contra=(\d+), abtineri=(\d+)(?:, nu au votat=(\d+))?")


def fold(text: str) -> str:
    """Lowercase, no diacritics (both ş/ţ cedilla and ș/ț comma forms), one space."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def to_text(fragment: str) -> str:
    """HTML fragment -> readable one-line text."""
    fragment = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", fragment)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", htmllib.unescape(fragment)).strip()


def parse_date(text: str) -> date:
    return datetime.strptime(text, "%d.%m.%Y").date()


# ---- procedure page -------------------------------------------------------------


@dataclass
class Vote:
    pentru: int
    contra: int
    abtineri: int
    nu_au_votat: int | None = None


@dataclass
class Event:
    day: date
    kind: str  # adopted | rejected | deposited | sent_to_president | reexamination_requested
    #            | promulgated | ccr_referral | ccr_decision | other
    text: str
    chamber: str | None = None  # "Senat" | "Camera Deputaților" (adopted / rejected)
    vote: Vote | None = None
    decree: str | None = None  # promulgated: "720/2026"
    law: str | None = None  # promulgated: "156/2026", when the page says it


@dataclass
class Adoption:
    """One definitive adoption by Parliament (a law can have several rounds,
    e.g. after the President asks for reexamination)."""

    day: date
    chamber: str
    vote: Vote | None
    confirmed: bool  # True once "depunere la Secretarul general" followed it
    round: int
    steps: list[Event] = field(default_factory=list)  # every chamber vote in this round


@dataclass
class Procedure:
    idp: int
    number: str  # "PL-x nr. 499/2026"
    title: str
    decisional_chamber: str | None
    character: str | None  # organic | ordinar
    senate_number: str | None
    senate_url: str | None
    promulgation_text_url: str | None  # PDF "Forma pentru promulgare"
    events: list[Event] = field(default_factory=list)

    @property
    def url(self) -> str:
        return PROCEDURE_URL.format(idp=self.idp)

    def adoptions(self) -> list[Adoption]:
        """Definitive adoptions, one per round.

        A round ends with "depunere la Secretarul general": that deposit happens
        right after Parliament's definitive vote, so the LAST adoption before it
        is the definitive one, whichever chamber voted last (for some laws the
        first chamber votes again on its own articles, a day after the decisive
        chamber). An open round (voted, not deposited yet: the usual state for
        a few days) counts the decisive chamber's last vote, unconfirmed.
        """
        out: list[Adoption] = []
        last: Event | None = None
        steps: list[Event] = []
        for ev in self.events:
            if ev.kind in ("adopted", "rejected"):
                steps.append(ev)
                last = ev if ev.kind == "adopted" else None
            elif ev.kind == "deposited" and last is not None:
                out.append(Adoption(last.day, last.chamber, last.vote, True, len(out) + 1, steps))
                last, steps = None, []
        if last is not None and (
            self.decisional_chamber is None or last.chamber == self.decisional_chamber
        ):
            out.append(Adoption(last.day, last.chamber, last.vote, False, len(out) + 1, steps))
        return out

    def main_vote(self, adoption: Adoption) -> Event | None:
        """The vote to show the public: the last step with a vote count (the
        Senate's counts are not on this site)."""
        voted = [s for s in adoption.steps if s.kind == "adopted" and s.vote]
        return voted[-1] if voted else None

    def promulgation(self) -> Event | None:
        proms = [e for e in self.events if e.kind == "promulgated"]
        return proms[-1] if proms else None


CHAMBERS = {"senat": "Senat", "senatul": "Senat", "camera deputatilor": "Camera Deputaților"}


def classify(text: str) -> dict:
    """Event kind and details from the action text of one timeline row."""
    f = fold(text)
    out: dict = {"kind": "other"}
    # Not only at the start: "proiectul de lege pentru aprobarea ordonantei de
    # urgenta adoptat de Camera Deputatilor" (laws approving government
    # ordinances). But "Forma adoptata de Senat" is a document label, not a vote.
    if m := re.search(r"(?<!forma )\badoptat[a]? de (senat|camera deputatilor)\b", f[:300]):
        out = {"kind": "adopted", "chamber": CHAMBERS[m.group(1)]}
    elif m := re.search(
        r"(?<!forma )\brespins[a]? de (?:catre )?(senat|camera deputatilor)\b", f[:300]
    ):
        out = {"kind": "rejected", "chamber": CHAMBERS[m.group(1)]}
    elif f.startswith("depunere la secretarul general"):
        out = {"kind": "deposited"}
    elif f.startswith("trimitere la presedintele romaniei pentru promulgare"):
        out = {"kind": "sent_to_president"}
    elif "solicita reexaminarea" in f:
        out = {"kind": "reexamination_requested"}
    elif m := re.match(r"promulgat[a]? prin decret nr\.? ?(\d+/\d{4})", f):
        out = {"kind": "promulgated", "decree": m.group(1)}
        if law := re.search(r"devine legea nr\.? ?(\d+/\d{4})", f):
            out["law"] = law.group(1)
    elif "sesizare de neconstitutionalitate" in f:
        out = {"kind": "ccr_referral"}
    elif f.startswith("curtea constitutionala decide"):
        out = {"kind": "ccr_decision"}
    if v := VOTE_RE.search(f):
        out["vote"] = Vote(*(int(x) if x else None for x in v.groups()))
    return out


EVENT_START = re.compile(r'<tr valign="top"><td align="center"[^>]*>\s*(' + DATE_RE + r")\s*</td>")


def parse_events(page: str) -> list[Event]:
    """Timeline rows: each starts with a date cell; its text runs until the next one."""
    start = page.find("Derularea procedurii")
    end = page.find("var MAX_ROWS", start)
    body = page[start : end if end > 0 else len(page)]
    marks = list(EVENT_START.finditer(body))
    events = []
    for i, m in enumerate(marks):
        chunk = body[m.end() : marks[i + 1].start() if i + 1 < len(marks) else len(body)]
        text = to_text(chunk)
        events.append(Event(day=parse_date(m.group(1)), text=text[:500], **classify(text)))
    return events


def _field(page: str, label: str) -> str | None:
    m = re.search(
        r'<td bgcolor="#?fff0d8"[^>]*>\s*' + re.escape(label) + r"\s*</td>\s*<td[^>]*>(.*?)</td>",
        page,
        re.S,
    )
    return to_text(m.group(1)) if m else None


class NotAProcedurePage(ValueError):
    """A firewall CAPTCHA or an error page instead of the real content."""


def parse_procedure(page: str, idp: int) -> Procedure:
    number = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    if not number or "Derularea procedurii" not in page:
        raise NotAProcedurePage(f"idp={idp}: not a procedure page")
    title = re.search(r'<meta property="og:description" content="([^"]*)"', page)
    decisional = _field(page, "Camera decizionala:")
    senate = re.search(r'<a href="(https?://(?:www\.)?senat\.ro/[^"]+)"[^>]*>([^<]+)</a>', page)
    promulg = re.search(
        r'<a href="([^"]+\.pdf)"[^>]*>(?:(?!</tr>).)*?Forma pentru promulgare', page, re.S
    )
    decisional_name = None
    if decisional:
        decisional_name = CHAMBERS.get(fold(decisional), decisional)
    character = _field(page, "Caracter:")
    return Procedure(
        idp=idp,
        number=htmllib.unescape(number.group(1)) if number else "",
        title=htmllib.unescape(title.group(1)) if title else "",
        decisional_chamber=decisional_name,
        character=fold(character) if character else None,
        senate_number=senate.group(2).strip() if senate else None,
        senate_url=senate.group(1) if senate else None,
        promulgation_text_url=(
            urljoin(PROCEDURE_URL.format(idp=idp), promulg.group(1)) if promulg else None
        ),
        events=parse_events(page),
    )


# ---- list pages -------------------------------------------------------------------


@dataclass
class ListEntry:
    idp: int
    number: str  # "PL-x 499/29.06.2026"
    title: str
    extra: str  # list-specific column: "Lege 2/2026 05.01.2026", or a date


def parse_list(page: str) -> list[ListEntry]:
    """Any upl_pck2015.lista result page: one row per bill."""
    out = []
    for row in re.findall(r"(?s)<tr[^>]*>(.*?)</tr>", page):
        link = re.search(r"upl_pck2015\.proiect\?cam=2&(?:amp;)?idp=(\d+)", row)
        cells = [to_text(c) for c in re.findall(r"(?s)<td[^>]*>(.*?)</td>", row)]
        if link and len(cells) >= 4:
            out.append(ListEntry(int(link.group(1)), cells[1], cells[2], cells[3]))
    return out


# ---- promulgated laws (Repertoriul legislativ) ------------------------------------------


@dataclass
class ActRef:
    law: str  # "2/2026"
    day: date  # date of the law (promulgation)
    title: str
    ida: int  # act id in Repertoriul legislativ


def parse_acts_list(page: str) -> list[ActRef]:
    """dic/legis_acte_parlam2015?cam=0&tip=1&an=YYYY: laws with number, date, act id."""
    out = []
    for row in re.findall(r"(?s)<tr[^>]*>(.*?)</tr>", page):
        cells = [to_text(c) for c in re.findall(r"(?s)<td[^>]*>(.*?)</td>", row)]
        link = re.search(r"htp_act\?ida=(\d+)", row)
        if link and len(cells) >= 3 and (m := re.fullmatch(r"(\d+)/(" + DATE_RE + ")", cells[1])):
            day = parse_date(m.group(2))
            out.append(ActRef(f"{m.group(1)}/{day.year}", day, cells[2], int(link.group(1))))
    return out


@dataclass
class ActInfo:
    mo_number: str | None  # "2/2026" (Monitorul Oficial, Partea I)
    mo_day: date | None
    bill_ref: str | None  # "P.L. nr. 320/2025"


RO_MONTHS = {
    "ian": 1, "feb": 2, "mar": 3, "apr": 4, "mai": 5, "iun": 6,
    "iul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip


def parse_act(page: str) -> ActInfo:
    """legis_pck.htp_act?ida=N: "Publicare: M.Of. nr. 2/5 ian. 2026"."""
    text = to_text(page)
    mo = re.search(
        r"Publicare:\s*M\.\s?Of\.\s*nr\.\s*(\d+)\s*/\s*(\d{1,2})\s+([a-z]+)\.?\s+(\d{4})", text
    )
    ref = re.search(r"Camera Deputatilor:\s*(P\.L\. nr\. \d+/\d{4})", text)
    mo_number = mo_day = None
    if mo and (month := RO_MONTHS.get(fold(mo.group(3))[:3])):
        mo_day = date(int(mo.group(4)), month, int(mo.group(2)))
        mo_number = f"{mo.group(1)}/{mo.group(4)}"
    return ActInfo(mo_number, mo_day, ref.group(1) if ref else None)


# ---- daily electronic votes (candidate discovery) ---------------------------------

VOTE_DAY_URL = BASE + "/ords/pls/steno/evot2015.data?dat={ymd}&idl=1"
_VOTE_DAY_TOKEN = re.compile(
    r'data-label="Titlu PL"[^>]*>\s*<a href="[^"]*upl_pck2015\.proiect\?'
    r'(?:cam=2&(?:amp;)?)?idp=(\d+)"'
    r'|data-label="Descriere">\s*(Vot final)'
)


def parse_vote_day(page: str) -> list[int]:
    """Bills (idp) with a "Vot final" on the Chamber's vote page of one day.

    Catches laws adopted TODAY: they reach the status lists only after the
    deposit, 2-6 days later. (A final vote here can also be a first-chamber
    vote; the procedure page decides whether it is a definitive adoption.)"""
    out: list[int] = []
    current = None
    for m in _VOTE_DAY_TOKEN.finditer(page):
        if m.group(1):
            current = int(m.group(1))
        elif current is not None and current not in out:
            out.append(current)
    return out


def parse_law_extra(extra: str) -> tuple[str, date] | None:
    """'Lege 2/2026 05.01.2026' (column of the 'became laws' list) -> ('2/2026', date)."""
    m = re.fullmatch(r"Lege (\d+/\d{4}) (" + DATE_RE + ")", extra.strip())
    return (m.group(1), parse_date(m.group(2))) if m else None
