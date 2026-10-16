"""Central execution policy for outbound company-source transport.

The policy is deliberately enforced by the transport rather than only by CLI
parsing.  This keeps an offline fixture replay offline even when a caller
mistakenly selects an enrichment module.
"""
from __future__ import annotations

from enum import Enum
from urllib.parse import urlsplit
import threading
import time
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


class NetworkPolicy(str, Enum):
    OFFLINE = "offline"
    AUTHORIZED_PUBLIC_BRREG = "authorized_public_brreg"


def parse_policy(value: NetworkPolicy | str) -> NetworkPolicy:
    try:
        return value if isinstance(value, NetworkPolicy) else NetworkPolicy(value)
    except ValueError as exc:
        raise ValueError(f"Unknown network policy: {value!r}") from exc


def public_brreg_url(url: str) -> bool:
    """Accept only unauthenticated HTTPS requests to the public BRREG host."""
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "data.brreg.no"
        and parsed.port in (None, 443)
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


class ServiceCooldown:
    """Per-run host-scoped backoff, shared across all source-family waves."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._until: dict[str, float] = {}

    def limited(self, url: str, retry_after: str | None) -> None:
        seconds = float("inf")  # No usable policy: stop service for this run.
        if retry_after:
            try:
                value = float(retry_after)
                if math.isfinite(value):
                    seconds = max(0, value)
            except ValueError:
                try:
                    date = parsedate_to_datetime(retry_after)
                    if date.utcoffset() is not None:
                        seconds = max(0, (date - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        with self._lock:
            host = urlsplit(url).hostname or ""
            self._until[host] = max(self._until.get(host, 0), time.monotonic() + seconds)

    def blocked(self, url: str) -> bool:
        with self._lock:
            return time.monotonic() < self._until.get(urlsplit(url).hostname or "", 0)
