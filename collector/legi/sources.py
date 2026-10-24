"""Where the adopted text of a law can be downloaded, best source first."""

from __future__ import annotations

import re

SENATE_PDF = "https://www.senat.ro/legis/PDF/{year}/{yy}{number}{kind}.PDF"


def text_sources(rec: dict) -> list[tuple[str, str]]:
    """(label, url) candidates for the latest adopted text:
    1. Chamber: "Forma pentru promulgare" (Word PDF, real text);
    2. Senate: "forma trimisă la promulgare" (LP), usually a scan;
    3. Senate: "forma adoptată de Senat" (FS), for a law voted but not yet sent."""
    out = []
    if rec.get("text_pdf"):
        out.append(("camera: forma pentru promulgare", rec["text_pdf"]))
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
