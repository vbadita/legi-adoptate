"""The update sweep reaches the newest bills even when it runs out of budget.

Before: candidates went in ascending idp order and bills not adopted yet were
re-fetched on every run, so each run spent its whole budget on the same oldest
bills and never reached the laws adopted that day (seen 25-28 Oct 2026)."""

import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path

import pytest

from legi import cdep, cli, store
from legi.http import FetchError, PoliteClient

FIX = Path(__file__).parent / "fixtures"
OLD = list(range(1000, 1100))  # 100 pending bills, first-chamber votes only
NEW = 23500  # never seen before
VOTED = 23499  # final vote in the last sitting days


class NotAdopted:
    def adoptions(self):
        return []


@pytest.fixture
def env(tmp_path, monkeypatch):
    d = tmp_path / "data"
    shutil.copytree(FIX / "site_data", d)
    fetched: list[int] = []

    def fake_get(self, url):
        if self.requests >= self.budget:
            raise FetchError(f"request budget of {self.budget} reached")
        self.requests += 1
        m = re.search(r"idp=(\d+)", url)
        if m:
            fetched.append(int(m.group(1)))
        return url

    def fake_gather(client, data, years, vote_days):
        client.get("list")  # the lists and vote pages cost requests too
        return {i: None for i in [*OLD, NEW, VOTED]}, {VOTED}

    monkeypatch.setattr(PoliteClient, "get", fake_get)
    monkeypatch.setattr(cli, "gather_candidates", fake_gather)
    monkeypatch.setattr(cdep, "parse_procedure", lambda page, idp: NotAdopted())
    monkeypatch.setattr(cli, "fill_monitor", lambda client, data, checks, now: 0)
    monkeypatch.setattr(cli, "today", lambda: date(2026, 10, 28))
    return d, fetched


def run(d, budget):
    args = cli.argparse.Namespace(
        data=str(d), cache=None, budget=budget, vote_days=5, years=[], refresh_all=False
    )
    return cli.cmd_update(args)


def test_newest_first_when_the_budget_runs_out(env):
    d, fetched = env
    assert run(d, budget=20) == cli.EXIT_OK
    # just voted, then never seen, then the newest pending bills
    assert fetched[:2] == [VOTED, NEW]
    assert fetched[2:] == sorted(fetched[2:], reverse=True)
    assert len(fetched) == 20 - 1 - cli.MONITOR_RESERVE  # lists + reserve for column 3


def test_meta_is_written_after_a_budget_stop(env):
    d, _ = env
    assert run(d, budget=20) == cli.EXIT_OK
    meta = json.loads((d / "meta.json").read_text())
    assert meta["updated_at"].startswith(datetime.now(cli.TZ).strftime("%d.%m.%Y"))


def test_pending_bills_are_not_refetched_every_run(env):
    d, fetched = env
    run(d, budget=500)
    first = set(fetched)
    assert set(OLD) <= first
    fetched.clear()
    run(d, budget=500)  # a few hours later: only the bill voted recently again
    assert fetched == [VOTED]


def test_no_meta_when_the_lists_were_not_read(env):
    d, _ = env
    (d / "meta.json").unlink(missing_ok=True)
    assert run(d, budget=0) == cli.EXIT_ERRORS
    assert not (d / "meta.json").exists()


def test_checks_remember_pending_bills(env):
    d, _ = env
    run(d, budget=500)
    checks = store.Checks(d)
    assert str(OLD[0]) in checks.data["proc"]
