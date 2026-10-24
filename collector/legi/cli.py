"""Command line.

  python -m legi update    --data ../data [--cache DIR] [--budget 200] [--vote-days 3]
  python -m legi mo-check  --data ../data          (one Monitorul Oficial call per day)
  python -m legi report    --data ../data --since 2026-10-01

update is incremental: a law whose status is complete (adopted, promulgated,
published) is never fetched again, so a daily run needs only a few requests.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import cdep, monitor, store
from .http import Blocked, FetchError, PoliteClient

LIST_URL = cdep.BASE + "/ords/pls/proiecte/upl_pck2015.lista?cam=2&{query}"
ACTS_URL = cdep.BASE + "/ords/pls/dic/legis_acte_parlam2015?cam=0&tip=1&an={year}"
ACT_URL = cdep.BASE + "/ords/pls/legis/legis_pck.htp_act?ida={ida}"
PENDING_STATES = ("G", "Pp", "C", "R")  # deposited, at promulgation, at the CCR, sent back
TZ = ZoneInfo("Europe/Bucharest")

EXIT_OK, EXIT_ERRORS, EXIT_BLOCKED = 0, 1, 3


def log(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str), file=sys.stderr)


def today() -> date:
    return datetime.now(TZ).date()


def gather_candidates(client: PoliteClient, data: Path, years: list[int], vote_days: int) -> dict:
    """idp -> (law number, law date) when the 'became laws' list knows it, else None."""
    cands: dict[int, tuple[str, date] | None] = {}
    for year in years:
        for e in cdep.parse_list(client.get(LIST_URL.format(query=f"anl={year}"))):
            cands[e.idp] = cdep.parse_law_extra(e.extra)
    for state in PENDING_STATES:
        for e in cdep.parse_list(client.get(LIST_URL.format(query=f"std={state}"))):
            cands.setdefault(e.idp, None)
    day = today()
    for _ in range(vote_days):  # laws adopted in the last days are not in any list yet
        while day.weekday() >= 5:  # no plenary votes at the weekend
            day -= timedelta(days=1)
        for idp in cdep.parse_vote_day(
            client.get(cdep.VOTE_DAY_URL.format(ymd=day.strftime("%Y%m%d")))
        ):
            cands.setdefault(idp, None)
        day -= timedelta(days=1)
    for rec in store.load_all(data):  # anything stored but not complete is refreshed
        if not store.is_complete(rec):
            cands.setdefault(rec["idp"], None)
    return cands


def cmd_update(args: argparse.Namespace) -> int:
    data = Path(args.data)
    client = PoliteClient(cache_dir=Path(args.cache) if args.cache else None, budget=args.budget)
    years = sorted(
        {today().year, *args.years} | ({today().year - 1} if today().month == 1 else set())
    )
    stats = {"fetched": 0, "skipped_complete": 0, "not_adopted": 0, "written": 0, "errors": 0}
    try:
        cands = gather_candidates(client, data, years, args.vote_days)
        log("candidates", count=len(cands), years=years)
        for idp, law in sorted(cands.items()):
            prev = store.load(data, idp)
            if prev and store.is_complete(prev):
                stats["skipped_complete"] += 1
                continue
            try:
                proc = cdep.parse_procedure(client.get(cdep.PROCEDURE_URL.format(idp=idp)), idp)
            except cdep.NotAProcedurePage as exc:
                stats["errors"] += 1
                log("bad_page", idp=idp, error=str(exc))
                continue
            stats["fetched"] += 1
            if not proc.adoptions():  # first-chamber vote only, or rejected: not a law (yet)
                stats["not_adopted"] += 1
                continue
            if law is None and prev and prev.get("law"):
                law = (prev["law"]["number"], date.fromisoformat(prev["law"]["day"]))
            rec = store.build_record(proc, law, prev.get("monitor") if prev else None)
            if store.save_if_changed(data, rec):
                stats["written"] += 1
                log("law_written", idp=idp, number=rec["number"], law=rec["law"])
        stats["monitor_found"] = fill_monitor(client, data)
    except Blocked as exc:
        log("blocked", error=str(exc), requests=client.requests, **stats)
        return EXIT_BLOCKED
    except FetchError as exc:  # includes the request budget: the next run continues
        log("stopped", error=str(exc), requests=client.requests, **stats)
        return EXIT_OK if "budget" in str(exc) else EXIT_ERRORS
    log("update_done", requests=client.requests, **stats)
    return EXIT_ERRORS if stats["errors"] else EXIT_OK


def fill_monitor(client: PoliteClient, data: Path) -> int:
    """Column 3: law number -> act page in the Chamber's repertory -> 'Publicare: M.Of. nr...'."""
    missing = [r for r in store.load_all(data) if r.get("law") and not r.get("monitor")]
    found = 0
    for year in sorted({int(r["law"]["number"].split("/")[1]) for r in missing}):
        acts = {a.law: a for a in cdep.parse_acts_list(client.get(ACTS_URL.format(year=year)))}
        for rec in missing:
            act = acts.get(rec["law"]["number"])
            if act is None:  # not in the repertory yet: next run
                continue
            info = cdep.parse_act(client.get(ACT_URL.format(ida=act.ida)))
            if info.mo_number:
                rec["monitor"] = {
                    "number": info.mo_number,
                    "day": info.mo_day.isoformat(),
                    "verified": None,  # set by mo-check
                }
                store.save_if_changed(data, rec)
                found += 1
                log("monitor_found", idp=rec["idp"], law=rec["law"]["number"], mo=info.mo_number)
    return found


def cmd_mo_check(args: argparse.Namespace) -> int:
    """Confirm 'M.Of. nr. X din <date>' against Monitorul Oficial's own day list.
    Exactly one call per calendar day: the newest publication date not yet
    checked (so new laws are confirmed first, the backlog one day at a time)."""
    data = Path(args.data)
    mo_dir = data / "mo"
    stamp = mo_dir / "last_call.txt"
    if stamp.exists() and stamp.read_text().strip() == today().isoformat():
        log("mo_check_skipped", reason="already called today")
        return EXIT_OK
    laws = store.load_all(data)
    pending = sorted(
        {
            r["monitor"]["day"]
            for r in laws
            if r.get("monitor") and r["monitor"].get("verified") is None
        },
        reverse=True,
    )
    if not pending:
        log("mo_check_skipped", reason="nothing to verify")
        return EXIT_OK
    day = date.fromisoformat(pending[0])
    mo_dir.mkdir(parents=True, exist_ok=True)
    stamp.write_text(today().isoformat() + "\n")  # before the call: a failure still counts
    try:
        issues = monitor.fetch_issues(day)
    except Blocked as exc:
        log("mo_blocked", error=str(exc))
        (mo_dir / "BLOCKED").write_text(f"{datetime.now(UTC).isoformat()} {exc}\n")
        return EXIT_BLOCKED
    except FetchError as exc:
        log("mo_error", error=str(exc))
        return EXIT_ERRORS
    (mo_dir / f"{day.isoformat()}.json").write_text(
        json.dumps({"day": day.isoformat(), "partea_i": issues}, ensure_ascii=False, indent=1)
        + "\n"
    )
    mismatches = 0
    for rec in laws:
        mo = rec.get("monitor")
        if mo and mo["day"] == day.isoformat():
            mo["verified"] = mo["number"].split("/")[0] in issues
            mismatches += not mo["verified"]
            store.save_if_changed(data, rec)
            log("mo_verified", idp=rec["idp"], mo=mo["number"], ok=mo["verified"])
    log("mo_check_done", day=day, issues=issues, mismatches=mismatches)
    return EXIT_ERRORS if mismatches else EXIT_OK


def status_columns(rec: dict) -> tuple[str, str, str]:
    r = rec["rounds"][-1]
    v = r["public_vote"]
    adopted = f"{r['adopted_on']} {r['chamber']}" + ("" if r["confirmed"] else " (provizoriu)")
    if v:
        adopted += f", vot {v['chamber'].split()[0]}: {v['pentru']}-{v['contra']}-{v['abtineri']}"
    law, prom = rec.get("law"), rec.get("promulgation")
    promulgated = (
        f"Legea {law['number']} din {law['day']}"
        if law
        else (f"decret {prom['decree']} din {prom['day']}" if prom else "-")
    )
    mo = rec.get("monitor")
    flag = {True: " ✔", False: " ✘", None: ""}[mo.get("verified")] if mo else ""
    published = f"M.Of. {mo['number']} din {mo['day']}{flag}" if mo else "-"
    return adopted, promulgated, published


def cmd_report(args: argparse.Namespace) -> int:
    since = date.fromisoformat(args.since)
    rows = []
    for rec in store.load_all(Path(args.data)):
        for r in rec["rounds"]:
            if date.fromisoformat(r["adopted_on"]) >= since:
                rows.append(
                    (r["adopted_on"], rec["number"], *status_columns(rec), rec["title"][:60])
                )
    for row in sorted(rows):
        print(" | ".join(row[1:]))
    print(f"# {len(rows)} adoptions since {since}", file=sys.stderr)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="legi")
    sub = parser.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update", help="refresh laws from cdep.ro (incremental)")
    u.add_argument("--data", required=True)
    u.add_argument("--cache", help="cache fetched pages here (development)")
    u.add_argument("--budget", type=int, default=200, help="max network requests per run")
    u.add_argument("--vote-days", type=int, default=3, help="recent sitting days to scan")
    u.add_argument("--years", type=int, nargs="*", default=[], help="extra 'became laws' years")
    u.set_defaults(func=cmd_update)
    m = sub.add_parser("mo-check", help="one Monitorul Oficial call: confirm a publication day")
    m.add_argument("--data", required=True)
    m.set_defaults(func=cmd_mo_check)
    r = sub.add_parser("report", help="print the 3 status columns")
    r.add_argument("--data", required=True)
    r.add_argument("--since", required=True)
    r.set_defaults(func=cmd_report)
    args = parser.parse_args(argv)
    return args.func(args)
