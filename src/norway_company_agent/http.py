from __future__ import annotations

import json
import hashlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .network_policy import NetworkPolicy, parse_policy, public_brreg_url


@dataclass
class FetchResult:
    url: str
    status: int
    elapsed_ms: int
    bytes_received: int
    body: Any = None
    error: str | None = None
    content_sha256: str | None = None
    retrieved_at: str | None = None
    effective_at: str | None = None
    raw_bytes: bytes | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn redirects into visible HTTP responses for bounded live pilots."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def fetch_json(url: str, *, timeout: float = 20.0, attempts: int = 3,
               allow_redirects: bool = True,
               policy: NetworkPolicy | str = NetworkPolicy.OFFLINE) -> FetchResult:
    """Fetch a public BRREG JSON endpoint only under an explicit policy.

    Offline is the safe default.  Returning a visible transport failure (not
    a 404 or empty body) prevents callers from translating an unattempted
    source into an absence claim.
    """
    selected_policy = parse_policy(policy)
    if selected_policy is NetworkPolicy.OFFLINE:
        return FetchResult(url, 0, 0, 0, error="network policy offline", retrieved_at=_utc_now())
    if selected_policy is not NetworkPolicy.AUTHORIZED_PUBLIC_BRREG or not public_brreg_url(url):
        return FetchResult(url, 0, 0, 0, error="network policy rejected URL", retrieved_at=_utc_now())
    last_error = "request failed"
    last_status = 0
    last_elapsed = 0
    last_bytes = 0
    last_digest: str | None = None
    for attempt in range(attempts):
        started = time.monotonic()
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "builderr-signalpost-poc/0.1 (+https://builderr.ai)"},
        )
        try:
            opener = urllib.request.urlopen if allow_redirects else urllib.request.build_opener(_NoRedirect()).open
            with opener(request, timeout=timeout) as response:
                raw = response.read()
                elapsed = int((time.monotonic() - started) * 1000)
                return FetchResult(url, response.status, elapsed, len(raw), json.loads(raw), content_sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=_utc_now(), raw_bytes=raw)
        except urllib.error.HTTPError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            raw = exc.read()
            last_status = exc.code
            last_elapsed = elapsed
            last_bytes = len(raw)
            last_digest = hashlib.sha256(raw).hexdigest()
            if exc.code in {404, 410}:
                return FetchResult(url, exc.code, elapsed, len(raw), error=f"HTTP {exc.code}", content_sha256=last_digest, retrieved_at=_utc_now(), raw_bytes=raw)
            last_error = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = type(exc).__name__
        if attempt + 1 < attempts:
            time.sleep(0.4 * (2**attempt))
    return FetchResult(url, last_status, last_elapsed, last_bytes, error=last_error, content_sha256=last_digest, retrieved_at=_utc_now())
