"""Monitorul Oficial, Partea I: which issues appeared on a given day.

Legea 202/1998 makes the electronic Monitorul Oficial free ("gratuit si liber
... inclusiv pentru cautare, salvare, distribuire"). Its robots.txt disallows
/ramo_customs/ for crawlers; by the project owner's decision we call the
e-Monitor day list (get_mo.php) once a day, with these safeguards:
  - an honest User-Agent naming this project;
  - one POST per day (the caller enforces it: one stored file per day);
  - no cookies, session, nonce or header tricks; redirects are NOT followed
    (the issue pages and the /docs/ PDFs are never fetched);
  - any refusal (403, 429, CAPTCHA, login) raises Blocked: the run stops.
It only confirms the Chamber's "M.Of. nr. X din <date>" for each law.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from .http import USER_AGENT, Blocked, FetchError

GET_MO_URL = "https://monitoruloficial.ro/ramo_customs/emonitor/get_mo.php"
ISSUE_RE = re.compile(r'href="/Monitorul-Oficial--PI--([0-9A-Za-z]+)--(\d{4})\.html"')


def parse_issues(page: str) -> list[str]:
    """Partea I issue numbers ('2', '3Bis', ...) listed for one day."""
    out: list[str] = []
    for number, _year in ISSUE_RE.findall(page):
        if number not in out:
            out.append(number)
    return out


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # never follow: we only read this response's own body


_opener = urllib.request.build_opener(_NoRedirect)


def fetch_issues(day: date, opener=_opener.open, timeout: float = 30.0) -> list[str]:
    body = urllib.parse.urlencode({"today": day.isoformat()}).encode()
    req = urllib.request.Request(
        GET_MO_URL, data=body, method="POST", headers={"User-Agent": USER_AGENT}
    )
    try:
        with opener(req, timeout=timeout) as resp:
            page = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (
            301,
            302,
            303,
            307,
            308,
        ):  # the server answers 302 with the list in the body
            page = exc.read().decode("utf-8", "replace")
        elif exc.code in (401, 403, 429):
            raise Blocked(f"get_mo.php refused: HTTP {exc.code}") from None
        else:
            raise FetchError(f"get_mo.php: HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"get_mo.php: {type(exc).__name__}") from None
    if re.search(r"(?i)captcha|autentific|login", page):
        raise Blocked("get_mo.php answered with a CAPTCHA/login page")
    return parse_issues(page)
