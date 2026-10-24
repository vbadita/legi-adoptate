"""Static site: data/ -> plain HTML files. Nothing runs per visitor.

  python -m legi site --data ../data --out ../site

Pages (under BASE):
  index.html              latest sitting day + date picker (a plain form: no JS)
  zi/YYYY-MM-DD.html      EVERY day of the coverage period: "no law adopted" is an answer too
  lege/<idp>.html         one law: explanation with quotes, rounds, timeline, sources
  despre.html             sources, method, limitations
  feed.xml, sitemap.xml
The build goes to a temporary directory that replaces the old one in one
rename, so nginx never serves a half-built site.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import status, store

BASE = "/legi-adoptate"
ORIGIN = "https://apps.badita.com"
COVERAGE_START = store.COVERAGE_START
in_coverage = store.in_coverage
HERE = Path(__file__).parent


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(HERE / "templates"),
        autoescape=select_autoescape(["html"]),  # every value is escaped by default
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["ro_date"] = status.ro_date
    env.filters["ro_weekday"] = status.ro_weekday
    env.globals["BASE"] = BASE
    env.globals["COVERAGE_START"] = COVERAGE_START.isoformat()
    return env


def _law_view(rec: dict, explanation: dict | None) -> dict:
    ok = explanation if explanation and explanation.get("status") == "ok" else None
    return {
        "rec": rec,
        "cols": status.columns(rec),
        "ex": ok["explanation"] if ok else None,
        "meta": ok,
        "url": f"{BASE}/lege/{rec['idp']}.html",
    }


def build(data_dir: Path, out_dir: Path, today: date | None = None) -> dict:
    today = today or date.today()
    env = _env()
    laws = [r for r in store.load_all(data_dir) if in_coverage(r)]
    views = {r["idp"]: _law_view(r, store.load_explanation(data_dir, r["idp"])) for r in laws}

    by_day: dict[str, list[tuple[dict, dict]]] = defaultdict(list)  # day -> [(view, round)]
    for rec in laws:
        for rnd in rec["rounds"]:
            by_day[rnd["adopted_on"]].append((views[rec["idp"]], rnd))
    for items in by_day.values():
        items.sort(key=lambda vr: vr[0]["rec"]["number"])
    sitting_days = sorted(d for d in by_day if date.fromisoformat(d) >= COVERAGE_START)
    meta_path = data_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    common = {"today": today.isoformat(), "start": COVERAGE_START.isoformat(), "meta": meta}

    tmp = Path(tempfile.mkdtemp(prefix=".site-", dir=out_dir.parent))
    (tmp / "zi").mkdir()
    (tmp / "lege").mkdir()
    shutil.copy(HERE / "static" / "style.css", tmp / "style.css")

    def write(rel: str, template: str, **ctx) -> None:
        (tmp / rel).write_text(env.get_template(template).render(**common, **ctx), encoding="utf-8")

    # every day of the coverage period, newest last
    day = COVERAGE_START
    while day <= today:
        iso = day.isoformat()
        prev_days = [d for d in sitting_days if d < iso]
        next_days = [d for d in sitting_days if d > iso]
        write(
            f"zi/{iso}.html",
            "day.html",
            day=iso,
            items=by_day.get(iso, []),
            prev_day=prev_days[-1] if prev_days else None,
            next_day=next_days[0] if next_days else None,
        )
        day += timedelta(days=1)

    for v in views.values():
        write(f"lege/{v['rec']['idp']}.html", "law.html", v=v)

    latest = sitting_days[-1] if sitting_days else None
    recent = [(d, len(by_day[d])) for d in reversed(sitting_days[-15:])]
    write("index.html", "index.html", latest=latest, items=by_day.get(latest, []), recent=recent)
    write("despre.html", "about.html", law_count=len(laws))
    write("404.html", "404.html")
    (tmp / "feed.xml").write_text(_feed(by_day, sitting_days), encoding="utf-8")
    (tmp / "sitemap.xml").write_text(_sitemap(views, sitting_days), encoding="utf-8")
    # which build is live: CI compares this with the commit it just built
    (tmp / "version.json").write_text(
        json.dumps(
            {
                "commit": os.environ.get("GITHUB_SHA", "local")[:12],
                "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "laws": len(laws),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    os.chmod(tmp, 0o755)
    old = out_dir.with_name(out_dir.name + ".old")
    if out_dir.exists():
        os.replace(out_dir, old)
    os.replace(tmp, out_dir)  # one rename: the new site appears complete
    shutil.rmtree(old, ignore_errors=True)
    return {
        "laws": len(laws),
        "day_pages": (today - COVERAGE_START).days + 1,
        "sitting_days": len(sitting_days),
    }


def _feed(by_day: dict, sitting_days: list[str]) -> str:
    """Atom feed: the 50 most recent adoptions."""
    entries = []
    for d in reversed(sitting_days):
        for v, rnd in by_day[d]:
            title = v["ex"]["titlu_simplu"] if v["ex"] else v["rec"]["title"]
            summary = v["ex"]["pe_scurt"] if v["ex"] else v["rec"]["title"]
            entries.append(
                f"<entry><id>{ORIGIN}{v['url']}#r{rnd['round']}</id>"
                f"<title>{escape(title)}</title>"
                f'<link href="{ORIGIN}{v["url"]}"/>'
                f"<updated>{d}T12:00:00Z</updated>"
                f"<summary>{escape('Adoptată pe ' + status.ro_date(d) + '. ' + summary)}</summary>"
                "</entry>"
            )
            if len(entries) == 50:
                break
        if len(entries) == 50:
            break
    updated = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        f"<id>{ORIGIN}{BASE}/</id><title>Legi adoptate de Parlamentul României</title>"
        f'<link href="{ORIGIN}{BASE}/"/><updated>{updated}</updated>'
        + "".join(entries)
        + "</feed>\n"
    )


def _sitemap(views: dict, sitting_days: list[str]) -> str:
    urls = [f"{ORIGIN}{BASE}/", f"{ORIGIN}{BASE}/despre.html"]
    urls += [f"{ORIGIN}{BASE}/zi/{d}.html" for d in sitting_days]
    urls += [f"{ORIGIN}{v['url']}" for v in views.values()]
    body = "".join(f"<url><loc>{escape(u)}</loc></url>" for u in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n'
    )
