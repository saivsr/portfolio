"""Thin HTTP client for the PlusVibe REST API.

Notes
-----
- Base: https://api.plusvibe.ai/api/v1
- Auth: x-api-key header (NOT Bearer)
- Cloudflare blocks the default Python User-Agent, so we send a browser-ish one.
- Documented rate limit is 5 req/s; we sleep 0.21s after each call to stay under.
- Retries 429 and 5xx with exponential backoff.
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

import requests


BASE_URL = "https://api.plusvibe.ai/api/v1"
MIN_INTERVAL_SEC = 0.21  # ~5 req/sec
# Sanitization note: the employer domain in the UA string has been genericized.
USER_AGENT = "Mozilla/5.0 (outbound-dashboard-etl; +https://internal-tooling.example)"


class PVError(RuntimeError):
    """Raised on unrecoverable PlusVibe API errors."""


class PVClient:
    def __init__(self, api_key: str, workspace_id: str, *, max_retries: int = 5):
        if not api_key:
            raise ValueError("PVClient requires a non-empty api_key")
        self.api_key = api_key
        self.workspace_id = workspace_id
        self.max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({
            "x-api-key": api_key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        self._last_call_at = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < MIN_INTERVAL_SEC:
            time.sleep(MIN_INTERVAL_SEC - elapsed)

    def get(self, path: str, **params: Any) -> Any:
        """GET {BASE}{path}?params. Returns parsed JSON."""
        # workspace_id is always required; allow caller to override per-call.
        params.setdefault("workspace_id", self.workspace_id)
        qs = urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{BASE_URL}{path}?{qs}" if qs else f"{BASE_URL}{path}"

        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = self._session.get(url, timeout=30)
            except requests.RequestException as exc:
                if attempt == self.max_retries - 1:
                    raise PVError(f"network error on {url}: {exc}") from exc
                time.sleep(2 ** attempt)
                continue
            finally:
                self._last_call_at = time.monotonic()

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            raise PVError(
                f"PV {resp.status_code} on {url}: {resp.text[:300]}"
            )

        raise PVError(f"PV repeatedly failed on {url}")
