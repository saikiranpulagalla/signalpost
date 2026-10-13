"""Optional single-homepage HTTPS acquisition with pinned public DNS and fail-closed robots.

Requires a site be explicitly enabled in a source-rights allowlist. No redirects,
proxies, auth, IP literals, JavaScript or third-party URLs. Production network
behavior is not certified by offline tests.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit
import time
import hashlib
from datetime import datetime, timezone
from v2_web.robots import robots_permission
from v2_web.identity import Candidate, normalize_url

USER_AGENT = 'Signalpost-Research/0.2 (source-rights-controlled)'
MAX_BYTES = 512 * 1024


@dataclass(frozen=True)
class HTTPResult:
    status: int
    body: bytes
    content_type: str
    content_encoding: str = "identity"


def resolve_public(host: str) -> str:
    addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('Nonpublic/mixed DNS resolution blocked')
    return sorted(addresses)[0]


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, timeout: float):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self.pinned_address = address

    def connect(self):
        sock = socket.create_connection((self.pinned_address, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _network_get(host: str, ip: str, path: str, *, timeout: float, max_bytes: int) -> HTTPResult:
    client = _PinnedHTTPS(host, ip, timeout)
    try:
        client.request('GET', path, headers={'Host': host, 'User-Agent': USER_AGENT,
                                            'Accept-Encoding': 'identity', 'Connection': 'close'})
        response = client.getresponse()
        if response.getheader('Content-Length') and int(response.getheader('Content-Length')) > max_bytes:
            raise ValueError('Excessive content length')
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError('Response too large')
        return HTTPResult(response.status, body, response.getheader('Content-Type', ''), response.getheader('Content-Encoding', 'identity'))
    finally:
        client.close()


@dataclass(frozen=True)
class FetchedPage:
    """Diagnostic source receipt; not a transferable trusted publication token."""
    request_url: str
    final_url: str
    pinned_ip: str
    body: bytes
    body_sha256: str
    retrieved_at: str
    content_type: str
    robots_decision: str
    source_mode: str = 'LOCAL_ACQUISITION_DIAGNOSTIC'
    publishable: bool = False


def fetch_one_homepage(candidate: Candidate, *, allowed_hosts: set[str], timeout: float = 4.0,
                       resolver=resolve_public, transport=_network_get, return_receipt: bool = False):
    """One bounded, authorized homepage request; robots and homepage use one IP.

    In-process injected transports are only diagnostics. A Python thread cannot
    forcibly interrupt a blocking custom fetcher, so no hard real-time guarantee.
    """
    url = normalize_url(candidate.url)
    parsed = urlsplit(url)
    host = parsed.hostname
    if host not in allowed_hosts:
        raise PermissionError('Source rights/host not explicitly approved')
    if not isinstance(timeout, (float, int)) or not 0 < timeout <= 10:
        raise ValueError('Invalid total request budget')
    started = time.monotonic()
    ip = resolver(host)
    if not ipaddress.ip_address(ip).is_global:
        raise ValueError('Resolver returned a nonpublic IP')
    def remaining():
        budget = float(timeout) - (time.monotonic() - started)
        if budget <= 0:
            raise TimeoutError('Shared robots+homepage deadline exceeded')
        return budget
    robots = transport(host, ip, '/robots.txt', timeout=remaining(), max_bytes=64 * 1024)
    if not isinstance(robots, HTTPResult) or not isinstance(robots.body, bytes) or len(robots.body) > 64 * 1024:
        raise PermissionError('Malformed or oversized robots response')
    if robots.status == 404:
        decision = 'robots_absent_404'
    elif robots.status == 200:
        allowed, decision = robots_permission(robots.body, user_agent=USER_AGENT, url=url)
        if not allowed:
            raise PermissionError('robots.txt restriction: ' + decision)
    else:
        raise PermissionError('robots.txt unavailable, throttled or access denied')
    path = parsed.path or '/'
    result = transport(host, ip, path, timeout=remaining(), max_bytes=MAX_BYTES)
    remaining()  # Reject responses that crossed the shared deadline.
    if not isinstance(result, HTTPResult) or not isinstance(result.body, bytes) or len(result.body) > MAX_BYTES:
        raise ValueError('Malformed or oversized HTTP response')
    mime = result.content_type.partition(';')[0].strip().lower()
    if result.status != 200 or mime != 'text/html':
        raise ValueError('Non-HTML response, redirect or non-success status')
    if result.content_encoding.strip().lower() not in ('', 'identity') or result.body[:2] == b'\x1f\x8b':
        raise ValueError('Compressed response not supported by bounded HTML verifier')
    if return_receipt:
        return FetchedPage(url, url, ip, result.body, hashlib.sha256(result.body).hexdigest(),
                           datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
                           mime, decision)
    return result.body


def verify_diagnostic_receipt(candidate: Candidate, page: FetchedPage, *,
                              allowed_hosts: set[str], evidence_cutoff: str | None = None):
    """Prevent accidental URL/body/host swaps in offline evidence classification.

    Deliberately NOT a security token: Python dataclasses are locally forgeable,
    and every returned Verdict is nonpublishable until a trusted runner binds
    acquisition, rights, identity and individual fact scope.
    """
    from v2_web.identity import verify_saved_page, Verdict
    if not isinstance(candidate, Candidate) or not isinstance(page, FetchedPage):
        raise ValueError('Missing candidate or diagnostic receipt')
    def block(reason):
        return Verdict('blocked', reason, candidate.organisation_number,
                       candidate.url, page.retrieved_at, None, (), None, False)
    try:
        host = urlsplit(normalize_url(candidate.url)).hostname
        if (host not in allowed_hosts or page.request_url != candidate.url or
            page.final_url != candidate.url or
            page.source_mode != 'LOCAL_ACQUISITION_DIAGNOSTIC' or page.publishable is not False or
            page.content_type != 'text/html' or
            not ipaddress.ip_address(page.pinned_ip).is_global or
            hashlib.sha256(page.body).hexdigest() != page.body_sha256):
            return block('diagnostic receipt source/bytes/rights mismatch')
    except (ValueError, TypeError, AttributeError):
        return block('malformed diagnostic receipt')
    return verify_saved_page(candidate, page.body, page.retrieved_at,
                             final_url=page.final_url, evidence_cutoff=evidence_cutoff)
