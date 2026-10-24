"""Command line: collect laws adopted by Parliament from cdep.ro.

  python -m legi adopted --since 2026-09-01 [--cache .cache]

Which bills to look at: every bill that became a law this year or last year,
plus every bill currently between the final vote and promulgation (status
lists G = deposited for constitutional challenge, Pp = at promulgation,
C = at the Constitutional Court, R = sent back by the President).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import cdep
from .http import PoliteClient

LIST_URL = cdep.BASE + "/ords/pls/proiecte/upl_pck2015.lista?cam=2&{query}"
PENDING_STATES = ("G", "Pp", "C", "R")


def candidate_bills(client: PoliteClient, years: list[int]) -> dict[int, str]:
    """idp -> where we found it."""
    found: dict[int, str] = {}
    for year in years:
        for e in cdep.parse_list(client.get(LIST_URL.format(query=f"anl={year}"))):
            found.setdefault(e.idp, f"lege {year}")
    for state in PENDING_STATES:
        for e in cdep.parse_list(client.get(LIST_URL.format(query=f"std={state}"))):
            found.setdefault(e.idp, f"stadiu {state}")
    return found


def cmd_adopted(args: argparse.Namespace) -> int:
    client = PoliteClient(cache_dir=Path(args.cache) if args.cache else None)
    since = date.fromisoformat(args.since)
    bills = candidate_bills(
        client, [since.year, date.today().year] if since.year != date.today().year else [since.year]
    )
    print(f"# {len(bills)} candidate bills", file=sys.stderr)
    rows = []
    for idp in sorted(bills):
        p = cdep.parse_procedure(client.get(cdep.PROCEDURE_URL.format(idp=idp)), idp)
        for a in p.adoptions():
            if a.day >= since:
                main = p.main_vote(a)
                vote = main.vote if main else None
                prom = p.promulgation()
                rows.append(
                    (
                        a.day,
                        a.chamber,
                        "da" if a.confirmed else "nu",
                        f"{vote.pentru}-{vote.contra}-{vote.abtineri}" if vote else "-",
                        (prom.law or prom.decree) if prom and prom.day >= a.day else "-",
                        p.number,
                        p.title[:70],
                    )
                )
    rows.sort()
    print("data_adoptarii | camera | depusa | vot(pentru-contra-abt) | lege | nr | titlu")
    for r in rows:
        print(" | ".join(str(x) for x in r))
    print(
        f"# {len(rows)} adoptions since {since}; {client.requests} HTTP requests", file=sys.stderr
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="legi")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("adopted", help="laws adopted by Parliament since a date")
    a.add_argument("--since", required=True, help="YYYY-MM-DD")
    a.add_argument("--cache", help="directory to cache fetched pages")
    a.set_defaults(func=cmd_adopted)
    args = parser.parse_args(argv)
    return args.func(args)
