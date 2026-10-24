import io
import urllib.error

import pytest

from legi.http import USER_AGENT, Blocked, FetchError, PoliteClient


class FakeSite:
    def __init__(self, responses):
        self.responses = list(responses)  # str body | int HTTP status | Exception
        self.requests = []

    def __call__(self, req, timeout):
        self.requests.append(req)
        r = self.responses.pop(0)
        if isinstance(r, int):
            raise urllib.error.HTTPError(req.full_url, r, "x", {}, None)
        if isinstance(r, Exception):
            raise r
        return io.BytesIO(r.encode())


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.now += s

    def __call__(self):
        return self.now


def client(site, clock, **kw):
    return PoliteClient(sleep=clock.sleep, clock=clock, opener=site, **kw)


def test_at_most_one_request_every_5_seconds():
    site, clock = FakeSite(["a", "b", "c"]), Clock()
    c = client(site, clock)
    assert [c.get("https://x.test/u1"), c.get("https://x.test/u2"), c.get("https://x.test/u3")] == [
        "a",
        "b",
        "c",
    ]
    assert clock.sleeps == [5.0, 5.0]


def test_honest_user_agent():
    site = FakeSite(["a"])
    client(site, Clock()).get("https://x.test/u")
    assert site.requests[0].get_header("User-agent") == USER_AGENT
    assert "github.com/vbadita/legi-adoptate" in USER_AGENT


def test_retries_server_errors_with_backoff():
    site, clock = FakeSite([503, TimeoutError(), "ok"]), Clock()
    c = client(site, clock)
    assert c.get("https://x.test/u") == "ok"
    assert c.requests == 3
    assert 5.0 in clock.sleeps and 10.0 in clock.sleeps


def test_no_retry_on_404():
    site = FakeSite([404])
    with pytest.raises(FetchError, match="HTTP 404"):
        client(site, Clock()).get("https://x.test/u")
    assert len(site.requests) == 1


def test_gives_up_after_retries():
    site = FakeSite([500, 500, 500, 500])
    with pytest.raises(FetchError, match="HTTP 500"):
        client(site, Clock(), retries=3).get("https://x.test/u")


def test_cache_means_one_request_per_page(tmp_path):
    site = FakeSite(["body"])
    c = client(site, Clock(), cache_dir=tmp_path)
    assert c.get("https://x.test/u") == "body" and c.get("https://x.test/u") == "body"
    assert c.requests == 1


def test_refuses_impolite_interval():
    with pytest.raises(ValueError):
        PoliteClient(min_interval=0.5)


def test_captcha_stops_the_run_and_is_not_cached(tmp_path):
    site = FakeSite(["<p>Firewall Captcha Authentication</p>"])
    c = client(site, Clock(), cache_dir=tmp_path)
    with pytest.raises(Blocked):
        c.get("https://x.test/u")
    assert list(tmp_path.iterdir()) == []


def test_request_budget():
    site = FakeSite(["a", "b"])
    c = client(site, Clock(), budget=1)
    c.get("https://x.test/u1")
    with pytest.raises(FetchError, match="budget"):
        c.get("https://x.test/u2")
