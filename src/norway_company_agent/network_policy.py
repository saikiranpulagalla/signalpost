"""Central execution policy for outbound company-source transport.

The policy is deliberately enforced by the transport rather than only by CLI
parsing.  This keeps an offline fixture replay offline even when a caller
mistakenly selects an enrichment module.
"""
from __future__ import annotations

from enum import Enum
from urllib.parse import urlsplit


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
