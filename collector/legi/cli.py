"""Command line.

  python -m legi update    --data ../data [--cache DIR] [--budget 200] [--vote-days 3]
  python -m legi mo-check  --data ../data          (one Monitorul Oficial call per day)
  python -m legi report    --data ../data --since 2026-10-01

update is incremental: a law whose status is complete (adopted, promulgated,
published) is never fetched again, so a daily run needs only a few requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import cdep, explain, monitor, sources, store
from .http import Blocked, FetchError, PoliteClient
from .text import is_scanned, page_count, page_images, pdf_to_text

LIST_URL = cdep.BASE + "/ords/pls/proiecte/upl_pck2015.lista?cam=2&{query}"
ACTS_URL = cdep.BASE + "/ords/pls/dic/legis_acte_parlam2015?cam=0&tip=1&an={year}"
ACT_URL = cdep.BASE + "/ords/pls/legis/legis_pck.htp_act?ida={ida}"
PENDING_STATES = ("G", "Pp", "C", "R")  # deposited, at promulgation, at the CCR, sent back
TZ = ZoneInfo("Europe/Bucharest")

EXIT_OK, EXIT_ERRORS, EXIT_BLOCKED = 0, 1, 3


def log(event: str, **fields) -> None:
    at = datetime.now(TZ).strftime("%H:%M:%S")  # shows where a slow run spent its time
    print(
        json.dumps({"at": at, "event": event, **fields}, ensure_ascii=False, default=str),
        file=sys.stderr,
        flush=True,
    )


def today() -> date:
    return datetime.now(TZ).date()


RECHECK_HOURS = 6  # an unfinished law is fetched at most every 6 hours (each daily run)
# A bill not adopted yet changes slowly; when the Chamber adopts it, it shows
# up on a vote page (fetched at once), so a slow re-check of the rest is enough:
# every second daily run (40, not 48: the timer's jitter would skip a day more).
PENDING_RECHECK_HOURS = 40
MONITOR_RESERVE = 8  # requests kept for column 3 (Monitorul Oficial) each run


def gather_candidates(
    client: PoliteClient, data: Path, years: list[int], vote_days: int
) -> tuple[dict, set[int]]:
    """(idp -> (law number, law date) or None, bills with a final vote in the
    last vote_days sitting days). Those are always fetched first."""
    cands: dict[int, tuple[str, date] | None] = {}
    voted_recently: set[int] = set()
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
            voted_recently.add(idp)
        day -= timedelta(days=1)
    for rec in store.load_all(data):  # anything stored but not complete is refreshed
        if not store.is_complete(rec) and store.in_coverage(rec):
            cands.setdefault(rec["idp"], None)
    return cands, voted_recently


def sweep_order(cands: dict, voted_recently: set[int], known: set[int]) -> list[int]:
    """Most likely to be news first: just voted, then never seen, then the
    newest bills. A run that hits its request budget still covers what matters
    (ascending order used to spend every run on the same oldest bills)."""
    return sorted(cands, key=lambda i: (i not in voted_recently, i in known, -i))


def cmd_update(args: argparse.Namespace) -> int:
    data = Path(args.data)
    client = PoliteClient(cache_dir=Path(args.cache) if args.cache else None, budget=args.budget)
    years = sorted(
        {today().year, *args.years} | ({today().year - 1} if today().month == 1 else set())
    )
    stats = {
        "fetched": 0,
        "skipped_complete": 0,
        "skipped_out_of_coverage": 0,
        "skipped_recent": 0,
        "not_adopted": 0,
        "written": 0,
        "errors": 0,
        "partial": False,
    }
    checks = store.Checks(data)
    now = datetime.now(TZ)
    cands = None
    try:
        cands, voted_recently = gather_candidates(client, data, years, args.vote_days)
        log("candidates", count=len(cands), years=years, voted_recently=len(voted_recently))
        known = {r["idp"] for r in store.load_all(data)} | {
            int(k) for k in checks.data.get("proc", {})
        }
        for idp in sweep_order(cands, voted_recently, known):
            law = cands[idp]
            prev = store.load(data, idp)
            if not args.refresh_all and idp not in voted_recently:
                if prev and store.is_complete(prev):
                    stats["skipped_complete"] += 1
                    continue
                if prev and not store.in_coverage(prev):  # e.g. a 2005 bill stuck at the CCR
                    stats["skipped_out_of_coverage"] += 1
                    continue
                hours = RECHECK_HOURS if prev else PENDING_RECHECK_HOURS
                if checks.recent("proc", idp, now, hours):
                    stats["skipped_recent"] += 1
                    continue
            if client.budget - client.requests <= MONITOR_RESERVE:
                stats["partial"] = True  # the rest waits for the next run, oldest last
                break
            try:
                proc = cdep.parse_procedure(client.get(cdep.PROCEDURE_URL.format(idp=idp)), idp)
                checks.mark("proc", idp, now)
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
        stats["monitor_found"] = fill_monitor(client, data, checks, now)
    except Blocked as exc:
        log("blocked", error=str(exc), requests=client.requests, **stats)
        return EXIT_BLOCKED
    except FetchError as exc:
        if "budget" not in str(exc) or cands is None:  # lists unread: not fresh
            log("stopped", error=str(exc), requests=client.requests, **stats)
            return EXIT_ERRORS
        stats["partial"] = True  # newest first: what is left is the oldest; next run
    finally:  # what was fetched stays recorded even if the run stops early
        # keep checks of known laws and of bills fetched but not adopted (yet)
        checks.save(
            {r["idp"] for r in store.load_all(data)} | {int(k) for k in checks.data.get("proc", {})}
        )
    store.save_json(
        data / "meta.json",
        {
            "updated_at": datetime.now(TZ).strftime("%d.%m.%Y, %H:%M"),
            "laws": len(store.load_all(data)),
        },
    )
    log("update_done", requests=client.requests, **stats)
    return EXIT_ERRORS if stats["errors"] else EXIT_OK


def fill_monitor(client: PoliteClient, data: Path, checks: store.Checks, now: datetime) -> int:
    """Column 3: law number -> act page in the Chamber's repertory -> 'Publicare: M.Of. nr...'."""
    missing = [
        r
        for r in store.load_all(data)
        if r.get("law")
        and not r.get("monitor")
        and not checks.recent("act", r["idp"], now, RECHECK_HOURS)
    ]
    found = 0
    for year in sorted({int(r["law"]["number"].split("/")[1]) for r in missing}):
        acts = {a.law: a for a in cdep.parse_acts_list(client.get(ACTS_URL.format(year=year)))}
        for rec in missing:
            act = acts.get(rec["law"]["number"])
            if act is None:  # not in the repertory yet: next run
                continue
            info = cdep.parse_act(client.get(ACT_URL.format(ida=act.ida)))
            checks.mark("act", rec["idp"], now)
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


MAX_TRIES = 3  # runs per law and prompt version before giving up on it
# Most laws have ~4 pages. A code of 490 pages took >8 min of CPU to extract
# here and ~400k tokens: over the free tier's per-minute limit (a 429) and it
# stalled every run from 26 to 28 Oct 2026. Such laws get no AI explanation.
MAX_PAGES = 100


def needs_explanation(rec: dict, existing: dict | None) -> bool:
    if not rec.get("rounds"):
        return False
    if existing is None:
        return True
    if (
        existing.get("prompt_version", 0) < explain.PROMPT_VERSION
        or existing.get("round") != rec["rounds"][-1]["round"]
        # the Chamber's newest text is not the one explained: a re-adopted text,
        # or a clean text that appeared after a Senate scan was used
        or (rec.get("text_pdf") and existing.get("source", {}).get("url") != rec["text_pdf"])
    ):
        return True
    # a rejection is often just a paraphrased quote: try again on later runs
    return existing.get("status") == "rejected" and existing.get("tries", 1) < MAX_TRIES


def cmd_explain(args: argparse.Namespace) -> int:
    """Plain-language explanations for laws that lack one, newest first.
    --daily-limit is a hard cap per calendar day (Bucharest), counted in
    data/usage.json, which is committed: several CI runs a day share it. The
    Gemini free quota is shared with other apps on the same key."""
    data = Path(args.data)
    usage_path = data / "usage.json"
    usage = json.loads(usage_path.read_text()) if usage_path.exists() else {}
    day = today().isoformat()
    used_today = usage.get("gemini_calls", {}).get(day, 0)
    allowed = min(args.limit, args.daily_limit - used_today)
    if allowed <= 0:
        log("explain_skipped", reason="daily limit reached", used_today=used_today)
        return EXIT_OK
    cache = Path(args.cache) if args.cache else None
    clients = {  # one polite client per site
        "cdep": PoliteClient(cache_dir=cache, budget=args.budget),
        "senat": PoliteClient(cache_dir=cache, budget=args.budget),
    }
    todo = [
        rec
        for rec in store.load_all(data)
        if needs_explanation(rec, store.load_explanation(data, rec["idp"]))
    ]
    todo.sort(key=lambda r: r["rounds"][-1]["adopted_on"], reverse=True)
    stats = {"todo": len(todo), "explained": 0, "rejected": 0, "no_source": 0, "too_long": 0}
    calls = usage.setdefault("gemini_calls", {})

    def record_usage(in_flight: int = 0) -> None:
        # saved before and after every call: a run killed by a time limit still
        # counts the calls it made
        calls[day] = used_today + stats["explained"] + stats["rejected"] + in_flight
        for old_day in sorted(calls)[:-31]:  # keep a month of history
            del calls[old_day]
        store.save_json(usage_path, usage)

    # Gemini can take 2 x 120 s per law; stop starting new laws well before the
    # service's 45 min timeout kills the run (and with it the commit).
    deadline = time.monotonic() + args.max_minutes * 60
    exit_code: int | None = None
    for rec in todo:
        if stats["explained"] + stats["rejected"] >= allowed:
            break
        if time.monotonic() > deadline:
            log("explain_deadline", minutes=args.max_minutes, **stats)
            break
        source = None
        log("fetching_text", idp=rec["idp"])
        for label, url in sources.text_sources(rec):
            client = clients["senat" if "senat.ro" in url else "cdep"]
            try:
                source = (label, url, client.get_pdf(url))
                break
            except Blocked as exc:
                log("blocked", error=str(exc), **stats)
                exit_code = EXIT_BLOCKED
                break
            except FetchError as exc:
                if "budget" in str(exc):
                    log("stopped", error=str(exc), **stats)
                    exit_code = EXIT_OK
                    break
                log("source_unavailable", idp=rec["idp"], url=url, error=str(exc))
        if exit_code is not None:
            break  # stop the run; Gemini usage so far is still recorded below
        if source is None:
            stats["no_source"] += 1
            continue
        label, url, pdf = source
        result_base = {
            "idp": rec["idp"],
            "round": rec["rounds"][-1]["round"],
            "source": {"label": label, "url": url, "sha256": hashlib.sha256(pdf).hexdigest()},
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "prompt_version": explain.PROMPT_VERSION,
        }
        pages = page_count(pdf)
        if pages > MAX_PAGES:  # not retried: only a new text or prompt brings it back
            store.save_json(
                store.explanation_path(data, rec["idp"]),
                {**result_base, "status": "too_long", "pages": pages},
            )
            stats["too_long"] += 1
            log("too_long", idp=rec["idp"], pages=pages)
            continue
        scanned = is_scanned(pdf)
        text = None if scanned else pdf_to_text(pdf)
        if text is not None and len(text) < 200:  # no usable text layer: read it as images
            text, scanned = None, True
        result_base["scanned"] = scanned
        log("asking_gemini", idp=rec["idp"], chars=len(text or ""), scanned=scanned)
        record_usage(in_flight=1)
        try:
            r = explain.explain(
                text=text, images=page_images(pdf) if scanned else None, title=rec["title"]
            )
        except explain.QuotaExhausted:
            log("gemini_quota_exhausted", **stats)
            break  # tomorrow; usage is still recorded below
        except explain.ExplainError as exc:
            stats["rejected"] += 1
            prev = store.load_explanation(data, rec["idp"]) or {}
            same = (
                prev.get("prompt_version") == explain.PROMPT_VERSION
                and prev.get("round") == result_base["round"]
            )
            tries = prev.get("tries", 1) + 1 if same and prev.get("status") == "rejected" else 1
            store.save_json(
                store.explanation_path(data, rec["idp"]),
                {
                    **result_base,
                    "status": "rejected",
                    "tries": tries,
                    "reason": str(exc),
                    "prompt_version": explain.PROMPT_VERSION,
                },
            )
            log("explanation_rejected", idp=rec["idp"], reason=str(exc))
            record_usage()
            continue
        if text is not None:
            store.save_text(data, rec["idp"], text)
        store.save_json(
            store.explanation_path(data, rec["idp"]), {**result_base, "status": "ok", **r}
        )
        stats["explained"] += 1
        record_usage()
        log(
            "explained",
            idp=rec["idp"],
            number=rec["number"],
            attempts=r["attempts"],
            scanned=scanned,
        )
    record_usage()
    log(
        "explain_done",
        **stats,
        used_today=calls[day],
        requests=sum(c.requests for c in clients.values()),
    )
    return EXIT_OK if exit_code is None else exit_code


def cmd_site(args: argparse.Namespace) -> int:
    from . import site  # jinja2 only needed here

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    stats = site.build(Path(args.data), out, today=today())
    log("site_built", out=str(out), **stats)
    return EXIT_OK


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
    u.add_argument("--vote-days", type=int, default=5, help="recent weekdays to scan (a full week)")
    u.add_argument("--years", type=int, nargs="*", default=[], help="extra 'became laws' years")
    u.add_argument(
        "--refresh-all",
        action="store_true",
        help="re-parse complete laws too (after a parser fix)",
    )
    u.set_defaults(func=cmd_update)
    m = sub.add_parser("mo-check", help="one Monitorul Oficial call: confirm a publication day")
    m.add_argument("--data", required=True)
    m.set_defaults(func=cmd_mo_check)
    e = sub.add_parser("explain", help="plain-language explanations (Gemini), newest first")
    e.add_argument("--data", required=True)
    e.add_argument("--cache", help="cache fetched PDFs here (development)")
    e.add_argument("--limit", type=int, default=20, help="max Gemini explanations per run")
    e.add_argument("--daily-limit", type=int, default=20, help="max per calendar day, all runs")
    e.add_argument("--budget", type=int, default=60, help="max downloads per site per run")
    e.add_argument("--max-minutes", type=float, default=20, help="start no new law after this")
    e.set_defaults(func=cmd_explain)
    s = sub.add_parser("site", help="build the static site from data/")
    s.add_argument("--data", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_site)
    r = sub.add_parser("report", help="print the 3 status columns")
    r.add_argument("--data", required=True)
    r.add_argument("--since", required=True)
    r.set_defaults(func=cmd_report)
    args = parser.parse_args(argv)
    return args.func(args)
