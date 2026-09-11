"""Rate-limited HTTP client that archives every response before anything parses it.

The raw-first rule: a response is written to data/raw/ and recorded in the
raw_fetches table *before* it is handed back to a caller. Parsers then run
against disk. When a parser turns out to be wrong -- and with undocumented APIs
it will -- the fix is a re-parse, not a re-fetch of data that may have already
changed upstream.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import RAW_DIR, load_config

log = logging.getLogger(__name__)


class RateLimiter:
    """Minimum-interval limiter, shared across threads."""

    def __init__(self, per_sec: float) -> None:
        self._min_interval = 1.0 / per_sec if per_sec > 0 else 0.0
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self._min_interval:
                time.sleep(self._min_interval - delta)
            self._last = time.monotonic()


class RetryableStatus(Exception):
    """A status worth retrying (429 or 5xx)."""


class Fetcher:
    """One per source. Owns the session, the rate limit and the raw archive."""

    def __init__(
        self,
        source: str,
        rate_limit_per_sec: float,
        headers: dict[str, str] | None = None,
        raw_dir: Path | None = None,
    ) -> None:
        self.source = source
        self.limiter = RateLimiter(rate_limit_per_sec)
        self.raw_dir = (raw_dir or RAW_DIR) / source
        self.session = requests.Session()
        cfg = load_config()
        self.session.headers.update({"User-Agent": cfg["user_agent"]})
        if headers:
            self.session.headers.update(headers)
        # Populated by callers so fetches can be recorded; left None in tests.
        self.conn = None

    @retry(
        retry=retry_if_exception_type((RetryableStatus, requests.RequestException)),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _request(self, url: str, params: dict[str, Any] | None) -> requests.Response:
        self.limiter.wait()
        resp = self.session.get(url, params=params, timeout=30)
        if resp.status_code == 429 or resp.status_code >= 500:
            # Honour Retry-After when the server sends one.
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    time.sleep(min(float(retry_after), 60))
                except ValueError:
                    pass
            raise RetryableStatus(f"{resp.status_code} for {url}")
        return resp

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        label: str,
        archive: bool = True,
    ) -> Any:
        """Fetch JSON, archiving the raw body first.

        `label` names the subdirectory under data/raw/<source>/ and should
        identify the endpoint, not the specific resource, e.g. "element-summary".
        """
        resp = self._request(url, params)
        resp.raise_for_status()
        body = resp.content

        if archive:
            path = self._archive(label, body)
            self._record(url, resp.status_code, body, path)

        return json.loads(body)

    def _archive(self, label: str, body: bytes) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = self.raw_dir / label
        out_dir.mkdir(parents=True, exist_ok=True)
        # Include a content hash so repeat fetches within the same second do not
        # collide and identical payloads are obvious on disk.
        digest = hashlib.sha256(body).hexdigest()
        path = out_dir / f"{ts}-{digest[:12]}.json.gz"
        with gzip.open(path, "wb") as fh:
            fh.write(body)
        return path

    def _record(self, url: str, status: int, body: bytes, path: Path) -> None:
        if self.conn is None:
            return
        from .config import PROJECT_ROOT

        self.conn.execute(
            """
            INSERT INTO raw_fetches (url, source, fetched_at, http_status, sha256, path)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                url,
                self.source,
                datetime.now(timezone.utc).isoformat(),
                status,
                hashlib.sha256(body).hexdigest(),
                str(path.relative_to(PROJECT_ROOT)),
            ),
        )
        self.conn.commit()


def fpl_fetcher(conn=None) -> Fetcher:
    cfg = load_config()["fpl"]
    f = Fetcher("fpl", cfg["rate_limit_per_sec"])
    f.conn = conn
    return f


def pl_fetcher(conn=None) -> Fetcher:
    cfg = load_config()["pl_official"]
    # The pulselive API returns 403 without an Origin header. This is its
    # documented CORS behaviour for the public site, not an access control.
    f = Fetcher("pl_official", cfg["rate_limit_per_sec"], headers={"Origin": cfg["origin"]})
    f.conn = conn
    return f


def historical_fetcher(conn=None) -> Fetcher:
    cfg = load_config()["historical"]
    f = Fetcher("historical", cfg["rate_limit_per_sec"])
    f.conn = conn
    return f
