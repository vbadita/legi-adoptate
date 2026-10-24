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
    args = cli.argparse.Namespace(data=str(d), cache=None, limit=20, daily_limit=2, budget=60)
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
