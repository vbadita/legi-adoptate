"""The Gemini daily cap holds across runs (CI runs several times a day)."""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from legi import cli, explain
from legi.http import Blocked, PoliteClient

FIX = Path(__file__).parent / "fixtures"
PDF = (FIX / "pl499_forma_promulgare.pdf").read_bytes()


@pytest.fixture
def data(tmp_path, monkeypatch):
    d = tmp_path / "data"
    shutil.copytree(FIX / "site_data", d)
    shutil.rmtree(d / "explanations")  # all 3 laws need an explanation
    calls = []

    def fake_explain(**kw):
        calls.append(kw["title"])
        return {
            "explanation": {},
            "model": "m",
            "prompt_version": explain.PROMPT_VERSION,
            "attempts": 1,
            "checked_against_text": True,
        }

    monkeypatch.setattr(explain, "explain", fake_explain)
    monkeypatch.setattr(PoliteClient, "get_pdf", lambda self, url: PDF)
    monkeypatch.setattr(cli, "today", lambda: date(2026, 10, 24))
    return d, calls


def run(d, **kw):
    args = cli.argparse.Namespace(
        data=str(d), cache=None, limit=20, daily_limit=2, budget=60, max_minutes=20
    )
    for k, v in kw.items():
        setattr(args, k, v)
    return cli.cmd_explain(args)


def usage(d):
    return json.loads((d / "usage.json").read_text())["gemini_calls"]


def test_cap_holds_across_runs_and_resets_next_day(data, monkeypatch):
    d, calls = data
    assert run(d) == cli.EXIT_OK and len(calls) == 2
    assert run(d) == cli.EXIT_OK and len(calls) == 2  # same day: nothing more
    assert usage(d) == {"2026-10-24": 2}
    monkeypatch.setattr(cli, "today", lambda: date(2026, 10, 25))
    run(d)
    assert len(calls) == 3 and usage(d)["2026-10-25"] == 1  # the third law, next day


def test_usage_is_recorded_when_a_run_stops_early(data, monkeypatch):
    d, calls = data
    n = {"pdfs": 0}

    def pdf_then_blocked(self, url):
        n["pdfs"] += 1
        if n["pdfs"] > 1:
            raise Blocked("CAPTCHA")
        return PDF

    monkeypatch.setattr(PoliteClient, "get_pdf", pdf_then_blocked)
    assert run(d, daily_limit=20) == cli.EXIT_BLOCKED
    assert len(calls) == 1 and usage(d) == {"2026-10-24": 1}


def test_no_new_law_is_started_after_the_deadline(data, monkeypatch):
    """Gemini can be slow; the run must end (and commit) before systemd kills it."""
    d, calls = data
    clock = {"t": 0.0}
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock["t"])
    slow = explain.explain

    def eleven_minutes(**kw):
        clock["t"] += 11 * 60
        return slow(**kw)

    monkeypatch.setattr(explain, "explain", eleven_minutes)
    assert run(d, daily_limit=20, max_minutes=20) == cli.EXIT_OK
    assert len(calls) == 2  # started at 0 and 11 min; the third would start at 22 min
    assert usage(d) == {"2026-10-24": 2}


def test_usage_is_saved_before_the_run_is_killed(data, monkeypatch):
    """systemd's timeout kills the process: calls already made must stay counted."""
    d, calls = data
    real = explain.explain

    def killed_on_second(**kw):
        if len(calls) == 1:
            raise SystemExit("SIGTERM")
        return real(**kw)

    monkeypatch.setattr(explain, "explain", killed_on_second)
    with pytest.raises(SystemExit):
        run(d, daily_limit=20)
    # the call cut off mid-flight may have reached Gemini: it counts too
    assert len(calls) == 1 and usage(d) == {"2026-10-24": 2}


def test_a_giant_law_is_marked_too_long_not_sent_to_gemini(data, monkeypatch, tmp_path):
    """A 490-page code stalled every run (CPU) and would exceed the free token limit."""
    d, calls = data
    monkeypatch.setattr(cli, "page_count", lambda pdf: 490)
    monkeypatch.setattr(cli, "pdf_to_text", lambda pdf: pytest.fail("must not parse"))
    assert run(d, daily_limit=20) == cli.EXIT_OK
    assert calls == []
    saved = json.loads(next((d / "explanations").glob("*.json")).read_text())
    assert saved["status"] == "too_long" and saved["pages"] == 490
    assert run(d, daily_limit=20) == cli.EXIT_OK and calls == []  # not retried
    assert "2026-10-24" not in usage(d) or usage(d)["2026-10-24"] == 0

    from legi import site

    site.build(d, tmp_path / "site", today=date(2026, 10, 24))
    page = (tmp_path / "site" / "lege" / f"{saved['idp']}.html").read_text()
    assert "Legea are 490 de pagini" in page
