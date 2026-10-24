"""Columns 2 and 3: law number, Monitorul Oficial, storage, the daily M.Of. check."""

import io
import json
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from legi import cdep, cli, monitor, store
from legi.http import Blocked

FIX = Path(__file__).parent / "fixtures"


def read(name):
    return (FIX / name).read_text(encoding="utf-8")


# ---- discovery and law numbers ---------------------------------------------------


def test_vote_day_lists_bills_with_a_final_vote():
    idps = cdep.parse_vote_day(read("evot_20260916.html"))
    assert 22703 in idps  # PL-x 430/2025, "Vot final adoptare" 262-5-1
    assert 23228 in idps  # Pl-x 343/2026: a first-chamber final vote (decided later)
    assert 1429 not in idps  # a Chamber resolution (PHCD), not a bill
    assert len(idps) == len(set(idps))


def test_law_number_from_list_column():
    assert cdep.parse_law_extra("Lege 2/2026 05.01.2026") == ("2/2026", date(2026, 1, 5))
    assert cdep.parse_law_extra("20.10.2026") is None


# ---- Monitorul Oficial -------------------------------------------------------------


def test_issues_of_a_day():
    assert monitor.parse_issues(read("getmo_20260105.html")) == ["1", "1Bis", "2", "3", "3Bis"]


class FakeOpener:
    def __init__(self, result):
        self.result, self.requests = result, []

    def __call__(self, req, timeout):
        self.requests.append(req)
        if isinstance(self.result, Exception):
            raise self.result
        return io.BytesIO(self.result.encode())


def _http_error(code, body=""):
    return urllib.error.HTTPError(monitor.GET_MO_URL, code, "x", {}, io.BytesIO(body.encode()))


def test_get_mo_reads_the_302_body_without_following_it():
    opener = FakeOpener(_http_error(302, read("getmo_20260105.html")))
    assert monitor.fetch_issues(date(2026, 1, 5), opener=opener) == ["1", "1Bis", "2", "3", "3Bis"]
    [req] = opener.requests  # exactly one request, a POST with only the date
    assert req.get_method() == "POST" and req.data == b"today=2026-01-05"
    assert "github.com/vbadita/legi-adoptate" in req.get_header("User-agent")
    assert req.get_header("Cookie") is None


def test_redirects_are_never_followed():
    handler = monitor._NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://x/") is None


@pytest.mark.parametrize("code", [401, 403, 429])
def test_refusal_stops(code):
    with pytest.raises(Blocked):
        monitor.fetch_issues(date(2026, 1, 5), opener=FakeOpener(_http_error(code)))


def test_captcha_stops():
    with pytest.raises(Blocked):
        monitor.fetch_issues(date(2026, 1, 5), opener=FakeOpener("<p>Captcha</p>"))


# ---- storage -------------------------------------------------------------------------


def record_20798():
    proc = cdep.parse_procedure(read("proc_20798_adoptata.html"), 20798)
    return store.build_record(proc, ("156/2026", date(2026, 7, 20)), None)


def test_record_fields():
    rec = record_20798()
    [r] = rec["rounds"]
    assert (r["adopted_on"], r["chamber"], r["confirmed"]) == (
        "2026-06-30",
        "Camera Deputaților",
        True,
    )
    assert r["public_vote"] == {
        "chamber": "Camera Deputaților",
        "day": "2026-06-30",
        "pentru": 276,
        "contra": 1,
        "abtineri": 0,
        "nu_au_votat": None,
    }
    assert rec["law"] == {"number": "156/2026", "day": "2026-07-20"}
    assert rec["promulgation"] == {"day": "2026-07-20", "decree": "431/2026"}
    assert rec["monitor"] is None
    assert not store.is_complete(rec)  # not published yet as far as we know


def test_complete_after_publication():
    rec = record_20798()
    rec["monitor"] = {"number": "560/2026", "day": "2026-07-22", "verified": None}
    assert store.is_complete(rec)


def test_written_only_when_changed(tmp_path):
    rec = record_20798()
    assert store.save_if_changed(tmp_path, rec) is True
    assert store.save_if_changed(tmp_path, rec) is False  # identical: no write, no git diff
    rec["monitor"] = {"number": "560/2026", "day": "2026-07-22", "verified": None}
    assert store.save_if_changed(tmp_path, rec) is True
    assert store.load(tmp_path, 20798)["monitor"]["number"] == "560/2026"
    text = store.path_for(tmp_path, 20798).read_text(encoding="utf-8")
    assert json.loads(text) == rec and text.endswith("\n")
    assert list(tmp_path.glob("laws/.tmp-*")) == []  # atomic write left no temp file


def test_stable_layout():
    rec = record_20798()
    assert store.dumps(rec) == store.dumps(json.loads(store.dumps(rec)))
    assert '"chamber": "Camera Deputaților"' in store.dumps(rec)  # UTF-8, not \u escapes


# ---- the daily Monitorul Oficial check --------------------------------------------------


def published_law(tmp_path, mo_day="2026-01-05", mo_number="2/2026"):
    rec = record_20798()
    rec["monitor"] = {"number": mo_number, "day": mo_day, "verified": None}
    store.save_if_changed(tmp_path, rec)


def test_mo_check_confirms_and_calls_once_a_day(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "today", lambda: date(2026, 10, 24))
    monkeypatch.setattr(monitor, "fetch_issues", lambda day: calls.append(day) or ["1", "2", "3"])
    published_law(tmp_path)
    args = cli.argparse.Namespace(data=str(tmp_path))
    assert cli.cmd_mo_check(args) == cli.EXIT_OK
    assert calls == [date(2026, 1, 5)]
    assert store.load(tmp_path, 20798)["monitor"]["verified"] is True
    assert json.loads((tmp_path / "mo" / "2026-01-05.json").read_text())["partea_i"] == [
        "1",
        "2",
        "3",
    ]
    # a second run the same day makes no call at all
    published_law(tmp_path, mo_day="2026-01-06", mo_number="5/2026")
    assert cli.cmd_mo_check(args) == cli.EXIT_OK
    assert calls == [date(2026, 1, 5)]


def test_mo_check_flags_a_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "today", lambda: date(2026, 10, 24))
    monkeypatch.setattr(monitor, "fetch_issues", lambda day: ["1", "3"])
    published_law(tmp_path)  # the Chamber says nr. 2, the day list has no 2
    assert cli.cmd_mo_check(cli.argparse.Namespace(data=str(tmp_path))) == cli.EXIT_ERRORS
    assert store.load(tmp_path, 20798)["monitor"]["verified"] is False


def test_mo_check_blocked_is_recorded_and_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "today", lambda: date(2026, 10, 24))

    def refuse(day):
        raise Blocked("HTTP 403")

    monkeypatch.setattr(monitor, "fetch_issues", refuse)
    published_law(tmp_path)
    assert cli.cmd_mo_check(cli.argparse.Namespace(data=str(tmp_path))) == cli.EXIT_BLOCKED
    assert (tmp_path / "mo" / "BLOCKED").exists()
