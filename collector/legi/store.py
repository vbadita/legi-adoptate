"""One JSON file per law: data/laws/<idp>.json.

The files live in Git, so the repository history is the audit log: every
change of status (adopted -> promulgated -> published) is a dated commit.
Files are written only when their content changes, with sorted keys and a
stable layout, so a diff always means a real change.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path

from .cdep import Event, Procedure, Vote


def _vote(v: Vote | None) -> dict | None:
    if v is None:
        return None
    return {
        "pentru": v.pentru,
        "contra": v.contra,
        "abtineri": v.abtineri,
        "nu_au_votat": v.nu_au_votat,
    }


def _step(e: Event) -> dict:
    return {"day": e.day.isoformat(), "kind": e.kind, "chamber": e.chamber, "vote": _vote(e.vote)}


def build_record(
    proc: Procedure,
    law: tuple[str, date] | None,
    monitor: dict | None,
) -> dict:
    """Everything the site needs about one law. `law` comes from the
    'became laws' list (or the promulgation event); `monitor` is kept from the
    previous record (it only changes through the act page / the M.Of. check)."""
    rounds = []
    for a in proc.adoptions():
        main = proc.main_vote(a)
        rounds.append(
            {
                "round": a.round,
                "adopted_on": a.day.isoformat(),
                "chamber": a.chamber,
                "confirmed": a.confirmed,
                "public_vote": (
                    {"chamber": main.chamber, "day": main.day.isoformat(), **_vote(main.vote)}
                    if main
                    else None
                ),
                "steps": [_step(s) for s in a.steps],
            }
        )
    prom = proc.promulgation()
    if law is None and prom and prom.law:
        law = (prom.law, prom.day)
    return {
        "idp": proc.idp,
        "url": proc.url,
        "number": proc.number,
        "title": proc.title,
        "decisional_chamber": proc.decisional_chamber,
        "character": proc.character,
        "senate": {"number": proc.senate_number, "url": proc.senate_url}
        if proc.senate_number
        else None,
        "text_pdf": proc.promulgation_text_url,
        "rounds": rounds,
        "promulgation": {"day": prom.day.isoformat(), "decree": prom.decree} if prom else None,
        "law": {"number": law[0], "day": law[1].isoformat()} if law else None,
        "monitor": monitor,
        "timeline": [
            {"day": e.day.isoformat(), "kind": e.kind, "text": e.text} for e in proc.events
        ],
    }


def is_complete(rec: dict) -> bool:
    """Nothing left to learn from the Chamber's pages: every round confirmed,
    promulgated with a law number, and published (M.Of. known). Such laws are
    not fetched again, which keeps each run small and polite."""
    return (
        bool(rec.get("rounds"))
        and all(r["confirmed"] for r in rec["rounds"])
        and rec.get("law") is not None
        and rec.get("monitor") is not None
    )


def dumps(rec: dict) -> str:
    return json.dumps(rec, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def path_for(data_dir: Path, idp: int) -> Path:
    return data_dir / "laws" / f"{idp}.json"


def load(data_dir: Path, idp: int) -> dict | None:
    p = path_for(data_dir, idp)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_all(data_dir: Path) -> list[dict]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((data_dir / "laws").glob("*.json"), key=lambda p: int(p.stem))
    ]


def save_if_changed(data_dir: Path, rec: dict) -> bool:
    """Atomic write (temp file + rename), only when the content differs."""
    p = path_for(data_dir, rec["idp"])
    new = dumps(rec)
    if p.exists() and p.read_text(encoding="utf-8") == new:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(new)
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)
    return True
