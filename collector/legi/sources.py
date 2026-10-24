"""Where the adopted text of a law can be downloaded, best source first."""

from __future__ import annotations

import re

SENATE_PDF = "https://www.senat.ro/legis/PDF/{year}/{yy}{number}{kind}.PDF"


def text_sources(rec: dict) -> list[tuple[str, str]]:
    """(label, url) candidates for the latest adopted text:
    - if the Chamber has "Forma pentru promulgare" (Word PDF, real text): only that;
    - otherwise the Senate: "forma trimisă la promulgare" (LP), usually a scan,
      then "forma adoptată de Senat" (FS), for a law voted but not yet sent."""
    if rec.get("text_pdf"):
        # A clean Chamber text exists: use only that. If cdep.ro is unreachable
        # the law waits for the next run; a Senate scan would give an
        # explanation that cannot be checked against the text (seen in CI,
        # where cdep.ro blocks GitHub's network).
        return [("camera: forma pentru promulgare", rec["text_pdf"])]
    out = []
    senate = (rec.get("senate") or {}).get("number") or ""
    m = re.fullmatch(r"([A-Za-z]+\d+)/(\d{4})", senate)
    if m:
        number, year = m.group(1).upper(), m.group(2)
        for kind, label in (
            ("LP", "senat: forma trimisă la promulgare"),
            ("FS", "senat: forma adoptată"),
        ):
            out.append((label, SENATE_PDF.format(year=year, yy=year[2:], number=number, kind=kind)))
    return out
