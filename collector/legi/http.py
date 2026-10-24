"""Polite HTTP for public parliament sites: one request every 2 s at most,
a few retries with backoff on server errors, an honest User-Agent, and an
optional on-disk cache so a rerun never hits the site twice for one page."""

from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = "legi-adoptate/0.1 (+https://github.com/vbadita/legi-adoptate)"
RETRYABLE = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    pass


class Blocked(FetchError):
    """The site's firewall answered with a CAPTCHA: stop the whole run, retry
    another day, never try to get around it."""


BLOCK_MARKERS = ("Firewall Captcha",)


class PoliteClient:
    def __init__(
        self,
        min_interval: float = 5.0,
        budget: int = 200,
        retries: int = 3,
        timeout: float = 40.0,
        cache_dir: Path | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
        opener=urllib.request.urlopen,
    ) -> None:
        if min_interval < 2.0:
            raise ValueError("min_interval below 2 s is not polite")
        self.min_interval = min_interval
        # cdep.ro answered with a CAPTCHA after ~375 requests at 2 s (2026-10-24):
        # 5 s by default and a hard cap of network requests per run.
        self.budget = budget
        self.retries = retries
        self.timeout = timeout
        self.cache_dir = cache_dir
        self.sleep, self.clock, self.opener = sleep, clock, opener
        self._last = float("-inf")
        self.requests = 0  # network requests made (cache hits excluded)

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".html")

    def get(self, url: str) -> str:
        cached = self._cache_path(url)
        if cached and cached.exists():
            return cached.read_text(encoding="utf-8")
        if self.requests >= self.budget:
            raise FetchError(f"request budget of {self.budget} reached")
        for attempt in range(self.retries + 1):
            wait = self._last + self.min_interval - self.clock()
            if wait > 0:
                self.sleep(wait)
            self._last = self.clock()
            self.requests += 1
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with self.opener(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8", "replace")
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE or attempt == self.retries:
                    raise FetchError(f"{url}: HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == self.retries:
                    raise FetchError(f"{url}: {type(exc).__name__}") from None
            self.sleep(min(60.0, 5.0 * 2**attempt))  # 5, 10, 20 s
        if any(marker in body for marker in BLOCK_MARKERS):
            raise Blocked(f"{url}: firewall CAPTCHA, stopping")
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(body, encoding="utf-8")
        return body
