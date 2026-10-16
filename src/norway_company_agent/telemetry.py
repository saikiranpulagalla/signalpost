"""Bounded per-request operational telemetry for authorised source runs.

The sink deliberately stores request metadata, never response bodies or
credentials.  It writes one JSON object at a time under a process-local lock,
so it can be consumed as a JSONL stream without retaining the run in memory.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def source_family(url: str) -> str:
    path = urlsplit(url).path
    if "/roller" in path:
        return "roles"
    if "/underenheter" in path:
        return "locations"
    if "/regnskapsregisteret/regnskap/" in path:
        return "financials"
    if "/konsernstruktur/" in path:
        return "group"
    if "/enheter/" in path:
        return "registry_live"
    return "official_unknown"


def source_organisation_number(url: str) -> str | None:
    """Extract only a public organisation number from known BRREG URL shapes."""
    parsed = urlsplit(url)
    pieces = [piece for piece in parsed.path.split("/") if piece]
    for piece in reversed(pieces):
        if piece.isdigit() and len(piece) == 9:
            return piece
    for item in parsed.query.split("&"):
        key, _, value = item.partition("=")
        if key == "overordnetEnhet" and value.isdigit() and len(value) == 9:
            return value
    return None


def classify_outcome(status: int, error: str | None) -> str:
    if error == "source request budget exhausted":
        return "source_budget_exhausted"
    if status == 429:
        return "rate_limited"
    if status in {301, 302, 303, 307, 308}:
        return "redirect_rejected"
    if status == 404:
        return "not_found"
    if error in {"TimeoutError", "socket.timeout"}:
        return "timeout"
    if error:
        return "transport_error"
    if status and 200 <= status < 300:
        return "success"
    return "transport_error" if error else "http_error"


class RequestTelemetry:
    """Thread-safe streaming JSONL writer for physical and rejected requests."""

    def __init__(self, *, run_id: str, output: Path | None) -> None:
        self.run_id = run_id
        self.output = output
        self._lock = threading.Lock()

    def record(
        self,
        *,
        url: str,
        physical_request_number: int | None,
        started_at: str,
        completed_at: str,
        duration_ms: int,
        status: int,
        bytes_received: int,
        error: str | None,
        attempt: int = 1,
        retry_count: int = 0,
        cache_hit: bool = False,
        retry_after: str | None = None,
    ) -> dict[str, Any]:
        parsed = urlsplit(url)
        row: dict[str, Any] = {
            "run_id": self.run_id,
            "organisation_number": source_organisation_number(url),
            "source_family": source_family(url),
            "host": parsed.hostname,
            "endpoint": parsed.path,
            "http_status": status or None,
            "error": error,
            "classification": classify_outcome(status, error),
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": max(0, int(duration_ms)),
            "bytes_received": max(0, int(bytes_received)),
            "attempt": attempt,
            "retry_count": retry_count,
            "physical_source_request_number": physical_request_number,
            "cache_hit": bool(cache_hit),
            "retry_after": retry_after,
        }
        if self.output is not None:
            with self._lock:
                self.output.parent.mkdir(parents=True, exist_ok=True)
                with self.output.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    handle.flush()
        return row
