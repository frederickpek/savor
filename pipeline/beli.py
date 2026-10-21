"""Beli access via ProjectBarks/beli-api.

The SDK's `BeliSession` handles login, token refresh, the browser User-Agent the backend
insists on, and host routing. Its generated endpoint functions are skipped on purpose: most
responses are typed as a bare `object`, and we want the raw JSON for snapshots and the schema
audit anyway. So calls go through the session's authenticated httpx client directly.
"""

import random
import time
from typing import Any

import httpx
from beli_api.beli import HOST_API, BeliSession

ORIGIN = "https://localhost"
# The community-polite floor is 350 ms, but at 0.4s Beli started answering 429 after ~150 calls.
MIN_INTERVAL = 1.25
# Two retries, ~3s then ~9s, same policy as a transient CDN blip.
BACKOFF = (3, 9)


class PermanentError(Exception):
    """A response that retrying won't fix (404, bad params, permission)."""


class RateLimited(RuntimeError):
    """HTTP 429. Bulk enrichment stops for the day on this rather than retrying through it."""


class Beli:
    def __init__(self, session: BeliSession) -> None:
        self.session = session
        self.user_id = session.user_id
        self.http = session.client(base_url=HOST_API).get_httpx_client()
        self.http.timeout = httpx.Timeout(30)
        self._last = 0.0
        self.calls = 0
        # Optional per-request hook: log(method, path, params, status, ms). None in the daily job.
        self.log = None

    @classmethod
    def login(cls, email: str, password: str) -> "Beli":
        return cls(BeliSession(email=email, password=password))

    def get(self, path: str, params: dict | None = None, patient: bool = True) -> Any:
        """patient=False raises RateLimited on the first 429 instead of backing off and retrying."""
        return self._request("GET", path, patient=patient, params=params)

    def post(self, path: str, body: dict) -> Any:
        return self._request("POST", path, json=body)

    def _request(self, method: str, path: str, patient: bool = True, **kw) -> Any:
        reauthed = False
        for attempt in range(len(BACKOFF) + 1):
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            t0 = time.monotonic()
            try:
                r = self.http.request(method, path, headers={"Origin": ORIGIN}, **kw)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                err: Exception = e
                if self.log:
                    self.log(method, path, kw.get("params"), type(e).__name__, (time.monotonic() - t0) * 1000)
            else:
                if self.log:
                    self.log(method, path, kw.get("params"), r.status_code, (time.monotonic() - t0) * 1000)
                if r.status_code == 401 and not reauthed:
                    # Access token died early; force a refresh/login and try once more.
                    self.session.tokens.access = ""
                    reauthed = True
                    continue
                if r.status_code < 400:
                    return r.json() if r.content else None
                if r.status_code != 429 and r.status_code < 500:
                    raise PermanentError(f"{method} {path}: HTTP {r.status_code} {r.text[:200]}")
                if r.status_code == 429:
                    err = RateLimited(f"{method} {path}: HTTP 429")
                    if not patient:
                        raise err
                else:
                    err = RuntimeError(f"{method} {path}: HTTP {r.status_code}")
            if attempt == len(BACKOFF):
                raise err
            pause = BACKOFF[attempt] * (0.8 + 0.4 * random.random())
            print(f"  retry {attempt + 1}/{len(BACKOFF)} in {pause:.0f}s: {err!r}")
            time.sleep(pause)
        raise RuntimeError("unreachable")
