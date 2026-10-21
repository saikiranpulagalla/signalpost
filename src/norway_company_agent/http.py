from __future__ import annotations

import json
import hashlib
import time
import socket
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from .network_policy import NetworkPolicy, parse_policy, public_brreg_url
from v0_kernel.json_codec import loads as strict_loads


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
    retry_after: str | None = None
    body_complete: bool = True
    request_started: bool | None = None


# A timed-out I/O worker has no access to profiles, telemetry or publication.
# It is a daemon and can never delay process exit. Until it settles, refuse
# further operations on that host: isolation must not grow live concurrency.
_isolated_lock = threading.Lock()
_isolated: dict[str, list[threading.Event]] = {}


def transport_stop_reason(url: str) -> str | None:
    host = urlsplit(url).hostname or ""
    with _isolated_lock:
        pending = [done for done in _isolated.get(host, []) if not done.is_set()]
        _isolated[host] = pending
        return "transport service stopped" if pending else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn redirects into visible HTTP responses for bounded live pilots."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def fetch_json(url: str, *, timeout: float = 20.0, attempts: int = 3,
               allow_redirects: bool = True,
               policy: NetworkPolicy | str = NetworkPolicy.OFFLINE,
               deadline: float | None = None, max_bytes: int = 2_000_000,
               on_rate_limit: Callable[[str | None], None] | None = None,
               on_request_start: Callable[[], str | None] | None = None) -> FetchResult:
    """Bound the whole operation, including headers, body and decoding.

    urllib's timeout alone is only an inactivity bound. A private daemon I/O
    worker plus an absolute monotonic wait also bounds slow headers/DNS/body.
    Cancellation shuts down an exposed socket where possible; an inaccessible
    stalled opener is isolated and quarantines its host until it settles.
    No cancelled worker may retry or mutate externally visible result state.
    """
    selected_policy = parse_policy(policy)
    if selected_policy is NetworkPolicy.OFFLINE:
        return FetchResult(url, 0, 0, 0, error="network policy offline", retrieved_at=_utc_now(), request_started=False)
    if selected_policy is not NetworkPolicy.AUTHORIZED_PUBLIC_BRREG or not public_brreg_url(url):
        return FetchResult(url, 0, 0, 0, error="network policy rejected URL", retrieved_at=_utc_now(), request_started=False)
    if timeout <= 0 or attempts < 1 or max_bytes < 1:
        raise ValueError("Positive timeout, attempts and response byte cap required")
    if transport_stop_reason(url):
        return FetchResult(url, 0, 0, 0, error="transport service stopped", retrieved_at=_utc_now(), request_started=False)
    operation_started = time.monotonic()
    expires = min(operation_started + timeout, deadline if deadline is not None else float("inf"))
    last = FetchResult(url, 0, 0, 0, error="execution deadline reserve reached", retrieved_at=_utc_now(), body_complete=False, request_started=False)
    for attempt in range(attempts):
        if time.monotonic() >= expires:
            break
        done, cancelled = threading.Event(), threading.Event()
        lock = threading.Lock()
        progress: dict[str, Any] = {"status": 0, "raw": bytearray(), "retry_after": None, "response": None, "request_started": False}
        outcome: list[FetchResult] = []

        def work() -> None:
            error = None
            complete = False
            body = None
            try:
                request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "builderr-signalpost-poc/0.1 (+https://builderr.ai)"})
                opener = urllib.request.urlopen if allow_redirects else urllib.request.build_opener(_NoRedirect()).open
                # The bounded callback only takes in-memory policy/counter locks.
                # Cancellation and start admission share this lock: a cancelled
                # worker cannot subsequently reserve or initiate another request.
                with lock:
                    if cancelled.is_set() or time.monotonic() >= expires:
                        error = "execution deadline reserve reached"
                        return
                    refusal = transport_stop_reason(url)
                    if not refusal and on_request_start:
                        refusal = on_request_start()
                    if refusal:
                        error = refusal
                        return
                    progress["request_started"] = True
                try:
                    response = opener(request, timeout=max(0.001, expires - time.monotonic()))
                except urllib.error.HTTPError as exc:
                    response = exc
                    error = f"HTTP {exc.code}"
                with lock:
                    progress["response"] = response
                    progress["status"] = response.code if isinstance(response, urllib.error.HTTPError) else response.status
                    headers = getattr(response, "headers", {})
                    progress["retry_after"] = headers.get("Retry-After")
                if cancelled.is_set():
                    response.close()
                    return
                if progress["status"] == 429 and on_rate_limit:
                    on_rate_limit(progress["retry_after"])
                try:
                    declared = headers.get("Content-Length")
                    if declared is not None and int(declared) > max_bytes:
                        raise ValueError("ResponseTooLarge")
                    while True:
                        if cancelled.is_set() or time.monotonic() >= expires:
                            raise TimeoutError()
                        with lock:
                            room = max_bytes + 1 - len(progress["raw"])
                        reader = getattr(response, "read1", None) or response.read
                        chunk = reader(min(16384, room))
                        if not isinstance(chunk, bytes):
                            raise ValueError("Invalid response bytes")
                        with lock:
                            progress["raw"].extend(chunk)
                            too_large = len(progress["raw"]) > max_bytes
                        if too_large:
                            raise ValueError("ResponseTooLarge")
                        if not chunk:
                            complete = True
                            break
                    if not error:
                        body = strict_loads(bytes(progress["raw"]))
                    if time.monotonic() >= expires:
                        raise TimeoutError()
                finally:
                    response.close()
            except Exception as exc:
                error = "ResponseTooLarge" if str(exc) == "ResponseTooLarge" else type(exc).__name__
            finally:
                with lock:
                    raw = bytes(progress["raw"])
                    result = FetchResult(url, progress["status"], round((time.monotonic() - operation_started) * 1000), len(raw),
                        body=body if not error else None, error=error, content_sha256=hashlib.sha256(raw).hexdigest() if raw or complete else None,
                        retrieved_at=_utc_now(), raw_bytes=raw if complete else None,
                        retry_after=progress["retry_after"], body_complete=complete,
                        request_started=progress["request_started"])
                if not cancelled.is_set():
                    outcome.append(result)
                done.set()

        thread = threading.Thread(target=work, name="brreg-bounded-io", daemon=True)
        thread.start()
        if done.wait(max(0, expires - time.monotonic())) and outcome:
            last = outcome[0]
        else:
            cancelled.set()
            with lock:
                raw = bytes(progress["raw"])
                response = progress["response"]
                last = FetchResult(url, progress["status"], round((time.monotonic() - operation_started) * 1000), len(raw),
                    error="TimeoutError" if progress["request_started"] else "execution deadline reserve reached",
                    retrieved_at=_utc_now(), retry_after=progress["retry_after"], body_complete=False,
                    request_started=progress["request_started"])
            with _isolated_lock:
                _isolated.setdefault(urlsplit(url).hostname or "", []).append(done)

            def close_socket() -> None:
                try:
                    sock = response.fp.raw._sock
                    sock.shutdown(socket.SHUT_RDWR)
                except (AttributeError, OSError):
                    pass
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
            threading.Thread(target=close_socket, name="brreg-cancel-io", daemon=True).start()
            break
        if last.error is None or last.request_started is False or last.status in {404, 410, 429}:
            break
        if attempt + 1 < attempts:
            time.sleep(min(0.4 * (2**attempt), max(0, expires - time.monotonic())))
    return last
