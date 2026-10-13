"""Official BRREG transport, starter normalization and independently checked claims.

Only fixed endpoint templates are used, never arbitrary URLs from source data.
The live transport refuses redirects and caps response size; no website crawler.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_company_agent.official import (  # noqa: E402
    BRREG_ACCOUNTS, BRREG_ENTITY, BRREG_ROLES, BRREG_SUBUNITS,
    normalize_financials, normalize_roles, normalize_locations, normalize_entity,
)
from norway_company_agent.network_policy import NetworkPolicy, parse_policy

MODULE_URLS = {
    "registry_live": BRREG_ENTITY,
    "roles": BRREG_ROLES,
    "locations": BRREG_SUBUNITS,
    "financials": BRREG_ACCOUNTS,
}
MAX_BYTES = 2 * 1024 * 1024
# Separate network-page and assembled-result caps; both are explicitly bounded.
MAX_COMBINED_BYTES = 16 * 1024 * 1024
MAX_CLAIMS = 80
FINANCIAL_SOURCE_PATHS = {
    "revenue": ("resultatregnskapResultat", "driftsresultat", "driftsinntekter", "sumDriftsinntekter"),
    "operating_result": ("resultatregnskapResultat", "driftsresultat", "driftsresultat"),
    "annual_result": ("resultatregnskapResultat", "aarsresultat"),
    "assets": ("eiendeler", "sumEiendeler"),
    "equity": ("egenkapitalGjeld", "egenkapital", "sumEgenkapital"),
    "debt": ("egenkapitalGjeld", "gjeldOversikt", "sumGjeld"),
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def sha(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def endpoint(module: str, org: str) -> str:
    if module not in MODULE_URLS or re.fullmatch(r"[0-9]{9}", org) is None:
        raise ValueError("Unsupported official endpoint")
    url = MODULE_URLS[module].format(org=org)
    if module == "locations":
        # Official BRREG API supports orgnr sorting; stabilize page boundaries.
        url += "&sort=organisasjonsnummer,ASC"
    return url


def require_official_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "data.brreg.no" or
            parsed.port not in (None, 443) or parsed.username or parsed.password or
            parsed.fragment or "\\" in url or any(ord(x) < 32 for x in url)):
        raise ValueError("Only fixed HTTPS BRREG endpoints are permitted")
    # Require *literal* URL equality to one of our own templates, not a user URL.
    if not any(url == endpoint(k, org) for k in MODULE_URLS for org in _extract_orgs(url)):
        raise ValueError("URL does not match a fixed source template")


def _extract_orgs(url: str) -> list[str]:
    return re.findall(r"(?<!\d)\d{9}(?!\d)", url)


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(request.full_url, code, "redirection blocked", headers, fp)


@dataclass(frozen=True)
class SourceResponse:
    module: str
    url: str
    status: int
    retrieved_at: str
    body: Any = None
    content_sha256: str | None = None
    error: str | None = None
    requests: int = 1
    # A source is trusted for publication only when its *transport* or its
    # local-fixture reader attests to the bytes behind both digests.  This is
    # never inferred from an arbitrary caller-supplied content_sha256.
    digest_kind: str = "unverified"
    body_sha256: str | None = None
    # Nonempty only for a rigorously assembled locations page collection.
    pages: tuple["SourceResponse", ...] = ()
    # Requests for continuation pages that failed validation/retrieval still count.
    failed_continuations: int = 0
    continuation_state: str | None = None
    continuation_http_status: int | None = None
    continuation_reason: str | None = None


def location_page_url(org: str, page: int) -> str:
    """Generate (never follow) an allowlisted continuation URL."""
    if type(page) is not int or page < 0 or page >= MAX_LOCATION_PAGES:
        raise ValueError("Invalid location page index")
    base = endpoint("locations", org)
    return base if page == 0 else f"{base}&page={page}"


def _page_shape(response: SourceResponse, expected_page: int) -> tuple[int, int, int] | None:
    """Validate page metadata before treating a page as belonging to a set."""
    if response.status != 200 or not isinstance(response.body, dict):
        return None
    page = response.body.get("page")
    items = response.body.get("_embedded", {}).get("underenheter") if isinstance(response.body.get("_embedded"), dict) else None
    if not isinstance(page, dict) or not isinstance(items, list):
        return None
    try:
        number, total, elements = (page["number"], page["totalPages"], page["totalElements"])
        if (any(type(x) is not int for x in (number, total, elements)) or
                number != expected_page or not 1 <= total <= 100000 or
                elements < len(items) or total <= number or len(items) > 1000 or
                any(not isinstance(x, dict) or not isinstance(x.get("organisasjonsnummer"), str) or
                    re.fullmatch(r"[0-9]{9}", x["organisasjonsnummer"]) is None for x in items) or
                len({x["organisasjonsnummer"] for x in items}) != len(items)):
            return None
    except (KeyError, TypeError):
        return None
    return total, elements, len(items)


MAX_LOCATION_PAGES = 10  # BRREG offset ceiling: size=1000, first 10 pages
MAX_LOCATION_RECORDS = 10000


def assemble_location_pages(org: str, pages: list[SourceResponse], *, failed_attempts: int = 0,
                            continuation_state: str | None = None,
                            continuation_http_status: int | None = None,
                            continuation_reason: str | None = None) -> SourceResponse:
    """Retain raw per-page attestations; mark incomplete, never invent completeness.

    Deliberately refuses to merge a malformed, nonconsecutive or inconsistent
    page set. A failed continuation may still leave valid first-page facts.
    """
    if (not pages or len(pages) > MAX_LOCATION_PAGES or
            type(failed_attempts) is not int or failed_attempts < 0 or failed_attempts > 1):
        raise ValueError("Invalid number of location pages")
    if continuation_state not in (None, "blocked", "failed", "budget_limited", "deadline_limited",
                                  "cutoff_limited", "inconsistent_pages", "size_limited", "page_depth_limit"):
        raise ValueError("Invalid continuation state")
    if continuation_http_status is not None and (type(continuation_http_status) is not int or
                                                not 0 <= continuation_http_status <= 599):
        raise ValueError("Invalid continuation HTTP status")
    data: list[Any] = []
    unique_orgs: set[str] = set()
    last_org: str | None = None
    expected_total = expected_elements = None
    for i, p in enumerate(pages):
        if p.url != location_page_url(org, i) or not verified_payload(p):
            raise ValueError("Untrusted or out-of-sequence page")
        shape = _page_shape(p, i)
        if shape is None:
            raise ValueError("Unverifiable pagination metadata")
        total, elements, _ = shape
        if expected_total is not None and (total != expected_total or elements != expected_elements):
            raise ValueError("Page set changed while crawling")
        expected_total, expected_elements = total, elements
        for record in p.body["_embedded"]["underenheter"]:
            key = record["organisasjonsnummer"]
            if key in unique_orgs:
                raise ValueError("Duplicated subunit across pages")
            if last_org is not None and key <= last_org:
                raise ValueError("Out-of-order subunit across sorted pages")
            unique_orgs.add(key)
            last_org = key
            data.append(record)
        if len(data) > MAX_LOCATION_RECORDS:
            raise ValueError("Page set exceeds location record bound")
    combined = {"_embedded": {"underenheter": data},
                "page": {"number": len(pages) - 1, "totalPages": expected_total,
                         "totalElements": expected_elements},
                "_pagination": {"fetchedPages": len(pages),
                                "complete": (len(pages) == expected_total and len(data) == expected_elements and
                                             failed_attempts == 0 and continuation_state is None)}}
    if len(canonical_bytes(combined)) > MAX_COMBINED_BYTES:
        raise ValueError("Combined page set exceeds bounded aggregate limit")
    return SourceResponse("locations", endpoint("locations", org), 200,
                          pages[-1].retrieved_at, combined,
                          sha([[(p.url, p.content_sha256) for p in pages], failed_attempts,
                              continuation_state, continuation_http_status, continuation_reason]),
                          None, sum(p.requests for p in pages) + failed_attempts, "verified-pages",
                          sha(combined), tuple(pages), failed_attempts, continuation_state,
                          continuation_http_status, continuation_reason)


def origin_for_fact(response: SourceResponse, locator: str) -> tuple[str, str | None, str]:
    """Actual origin URL, raw digest and source-local locator for an assertion."""
    if not response.pages:
        return response.url, response.content_sha256, locator
    match = re.fullmatch(r"\$\._embedded\.underenheter\[(\d+)\]", locator)
    if not match:
        raise ValueError("Not a location evidence locator")
    index = int(match[1])
    for page in response.pages:
        size = len(page.body["_embedded"]["underenheter"])
        if index < size:
            return page.url, page.content_sha256, f"$._embedded.underenheter[{index}]"
        index -= size
    raise ValueError("Location locator exceeds crawled pages")


def origin_observation_for_fact(response: SourceResponse, locator: str) -> tuple[str, str | None, str, str]:
    """Evidence URL, byte digest, page-local locator and original page retrieval time."""
    url, digest, local = origin_for_fact(response, locator)
    if not response.pages:
        return url, digest, local, response.retrieved_at
    for page in response.pages:
        if page.url == url:
            return url, digest, local, page.retrieved_at
    raise ValueError("Fact has no authenticated source page")


def _single_fetch_url(module: str, url: str, *, timeout: float, max_bytes: int) -> SourceResponse:
    if module == "locations":
        orgs = _extract_orgs(url)
        if len(orgs) != 1 or url not in {location_page_url(orgs[0], i) for i in range(MAX_LOCATION_PAGES)}:
            raise ValueError("Unapproved location pagination URL")
    else:
        require_official_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirects(),
                                         urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "SignalpostEvidenceAgent/1.0"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            if resp.geturl() != url:
                raise ValueError("Official source unexpectedly redirected")
            if resp.status != 200:
                return SourceResponse(module, url, resp.status, utc_now(), error=f"HTTP {resp.status}")
            declared = resp.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                raise ValueError("Official response larger than configured limit")
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError("Official response exceeds byte limit")
            body = json.loads(raw)
            return SourceResponse(module, url, resp.status, utc_now(), body=body,
                                  content_sha256=hashlib.sha256(raw).hexdigest(),
                                  digest_kind="wire-json", body_sha256=sha(body))
    except urllib.error.HTTPError as exc:
        return SourceResponse(module, url, exc.code, utc_now(), error=f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, ValueError,
            json.JSONDecodeError) as exc:
        return SourceResponse(module, url, 0, utc_now(), error=f"{type(exc).__name__}: {str(exc)[:160]}")


def live_fetch(module: str, org: str, *, timeout: float = 4.0,
               max_bytes: int = MAX_BYTES, max_requests: int = 1,
               policy: NetworkPolicy | str = NetworkPolicy.OFFLINE) -> SourceResponse:
    """Fixed official fetch. Locations may collect pages within hard request/time caps."""
    if type(max_requests) is not int or max_requests < 1:
        raise ValueError("Invalid source-request budget")
    if parse_policy(policy) is not NetworkPolicy.AUTHORIZED_PUBLIC_BRREG:
        return SourceResponse(module, endpoint(module, org), 0, utc_now(),
                              error="network policy offline", requests=0)
    began = time.monotonic()
    first = _single_fetch_url(module, endpoint(module, org), timeout=timeout, max_bytes=max_bytes)
    if module != "locations" or first.status != 200 or not verified_payload(first):
        return first
    shape = _page_shape(first, 0)
    if shape is None:
        return SourceResponse(module, endpoint(module, org), 0, first.retrieved_at,
                              error="invalid first-page pagination metadata", requests=first.requests)
    if shape[0] <= 1:
        # Even a single page must satisfy the same completeness rule.
        return assemble_location_pages(org, [first])
    total, _, _ = shape
    pages = [first]
    # Never blindly follow _links.next. All continuation URLs are computed locally.
    for i in range(1, min(total, max_requests, MAX_LOCATION_PAGES)):
        remaining = timeout - (time.monotonic() - began)
        if remaining <= 0.05:
            return assemble_location_pages(org, pages, continuation_state="deadline_limited")
        nxt = _single_fetch_url("locations", location_page_url(org, i),
                                 timeout=remaining, max_bytes=max_bytes)
        if nxt.status != 200 or not verified_payload(nxt):
            state = "blocked" if nxt.status in (401, 403, 429) else "failed"
            return assemble_location_pages(org, pages, failed_attempts=nxt.requests,
                                           continuation_state=state,
                                           continuation_http_status=nxt.status,
                                           continuation_reason=nxt.error or "unverified continuation")
        if _page_shape(nxt, i) is None or _page_shape(nxt, i)[:2] != shape[:2]:
            return assemble_location_pages(org, pages, failed_attempts=nxt.requests,
                                           continuation_state="inconsistent_pages",
                                           continuation_reason="pagination metadata changed")
        try:
            assemble_location_pages(org, pages + [nxt])
        except ValueError as exc:
            return assemble_location_pages(org, pages, failed_attempts=nxt.requests,
                                           continuation_state="size_limited" if "limit" in str(exc) else "inconsistent_pages",
                                           continuation_reason=str(exc)[:160])
        pages.append(nxt)
    if len(pages) < total:
        state = ("page_depth_limit" if len(pages) >= MAX_LOCATION_PAGES else
                 "budget_limited" if len(pages) >= max_requests else "deadline_limited")
        return assemble_location_pages(org, pages, continuation_state=state)
    return assemble_location_pages(org, pages)

def fixture_fetch(module: str, org: str, source: dict[str, Any]) -> SourceResponse:
    entry = source.get(org, {}).get(module)
    url = endpoint(module, org)
    if entry is None:
        return SourceResponse(module, url, 0, utc_now(), error="fixture has no module")
    if not isinstance(entry, dict):
        return SourceResponse(module, url, 0, utc_now(), error="malformed fixture")
    status = entry.get("status", 200)
    if type(status) is not int or not 0 <= status <= 599:
        return SourceResponse(module, url, 0, utc_now(), error="invalid fixture status")
    if module == "locations" and status == 200 and isinstance(entry.get("pages"), list):
        try:
            pages = []
            for i, raw in enumerate(entry["pages"]):
                blob = canonical_bytes(raw)
                if len(blob) > MAX_BYTES:
                    raise ValueError("oversized location page")
                pages.append(SourceResponse(module, location_page_url(org, i), 200,
                                            utc_now(), raw, sha(raw), None, 0,
                                            "canonical-json", sha(raw)))
            return assemble_location_pages(org, pages)
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            return SourceResponse(module, url, 0, utc_now(), error=f"invalid paged fixture: {exc}", requests=0)
    body = entry.get("body") if status == 200 else None
    try:
        blob = canonical_bytes(body) if status == 200 else None
        if blob is not None and len(blob) > MAX_BYTES:
            raise ValueError("oversized fixture")
    except (ValueError, TypeError, OverflowError) as exc:
        return SourceResponse(module, url, 0, utc_now(), error=f"bad fixture: {exc}")
    return SourceResponse(module, url, status, utc_now(), body,
                          hashlib.sha256(blob).hexdigest() if blob is not None else None,
                          None if status == 200 else f"fixture HTTP {status}", 0,
                          "canonical-json" if status == 200 else "unverified",
                          sha(body) if status == 200 else None)


def verified_payload(response: SourceResponse) -> bool:
    """Require trusted provenance plus intact parsed JSON, not just a hash-shaped string.

    `wire-json` attestations are issued only by live_fetch when it has read the
    bounded raw bytes and parsed them. `canonical-json` is for local fixtures.
    An arbitrary injected SourceResponse defaults to unverified and cannot
    publish claims. This is a local trust-boundary check, not a signature from
    Brønnøysundregistrene.
    """
    try:
        semantic = sha(response.body)
        if response.digest_kind == "verified-pages":
            if response.module != "locations" or not response.pages:
                return False
            orgs = _extract_orgs(response.url)
            if len(orgs) != 1 or response.url != endpoint("locations", orgs[0]):
                return False
            try:
                regenerated = assemble_location_pages(orgs[0], list(response.pages),
                                                     failed_attempts=response.failed_continuations,
                                                     continuation_state=response.continuation_state,
                                                     continuation_http_status=response.continuation_http_status,
                                                     continuation_reason=response.continuation_reason)
                return (response.body == regenerated.body and
                        response.content_sha256 == regenerated.content_sha256 and
                        response.body_sha256 == regenerated.body_sha256 and
                        response.requests == regenerated.requests and
                        response.retrieved_at == regenerated.retrieved_at and
                        response.continuation_state == regenerated.continuation_state and
                        response.continuation_http_status == regenerated.continuation_http_status and
                        response.continuation_reason == regenerated.continuation_reason)
            except (ValueError, TypeError, KeyError, OverflowError):
                return False
        if (response.status != 200 or response.digest_kind not in ("wire-json", "canonical-json") or
                response.body_sha256 != semantic or not isinstance(response.content_sha256, str) or
                re.fullmatch(r"[a-f0-9]{64}", response.content_sha256) is None):
            return False
        return (response.digest_kind == "wire-json" or response.content_sha256 == semantic)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return False


def _valid_text(value: Any, maximum: int = 300) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum and not any(ord(c) < 32 for c in value)


def _valid_amount(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= 10**16


def _valid_period(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    start = value.get("fraDato") or value.get("start")
    end = value.get("tilDato") or value.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        a, b = datetime.fromisoformat(start[:10]).date(), datetime.fromisoformat(end[:10]).date()
        if 1900 <= a.year <= b.year <= datetime.now(timezone.utc).year + 1 and a <= b:
            return f"{a.isoformat()}/{b.isoformat()}"
    except ValueError:
        pass
    return None


def extract(module: str, org: str, body: Any, *, frozen_legal_name: str | None) -> tuple[list[dict[str, Any]], str]:
    """Return safe candidate facts + status, reusing starter normalizers.

    No website or corporate-group ownership is inferred here. All claims are scoped
    to the target organisation number from the *official endpoint*, with checked
    entity assertions inside responses. Values are verified again from source bodies.
    """
    facts: list[dict[str, Any]] = []
    if module == "registry_live":
        if not isinstance(body, dict):
            raise ValueError("Expected entity object")
        value = normalize_entity(body)
        if str(value.get("organisation_number") or "") != org:
            return [], "ambiguous"
        if not _valid_text(value.get("name")):
            return [], "ambiguous"
        # Live legal identity might legitimately differ from a frozen snapshot;
        # record the discrepancy but do not silently replace the frozen anchor.
        # A changed legal name with the SAME authenticated organisation number
        # is a potential rename, not a different legal entity. Record the
        # observed name with its source timestamp, never overwrite the frozen
        # identity anchor in-place. NFC handles equivalent Norwegian spelling.
        if frozen_legal_name:
            observed = unicodedata.normalize("NFC", value["name"].strip()).casefold()
            frozen = unicodedata.normalize("NFC", frozen_legal_name.strip()).casefold()
            if observed != frozen:
                history = body.get("historiskeNavn", [])
                if not isinstance(history, list) or not any(
                    isinstance(item, dict) and isinstance(item.get("navn"), str) and
                    unicodedata.normalize("NFC", item["navn"].strip()).casefold() == frozen
                    for item in history
                ):
                    return [], "ambiguous"  # Must corroborate a legal rename.
        return [{"field": "official_registry_name", "value": value["name"].strip(),
                 "locator": "$.navn", "reporting_period": None}], "available"

    if module == "roles":
        if not isinstance(body, dict) or not isinstance(body.get("rollegrupper"), list):
            raise ValueError("Malformed roles payload")
        normalized = normalize_roles(body)["roles"]
        cursor = 0
        for group_index, group in enumerate(body["rollegrupper"]):
            if not isinstance(group, dict) or not isinstance(group.get("roller", []), list):
                raise ValueError("Malformed group or role list")
            for role_index, item in enumerate(group.get("roller", [])):
                if not isinstance(item, dict) or cursor >= len(normalized):
                    raise ValueError("Malformed role member")
                role = normalized[cursor]
                cursor += 1
                name, code = role.get("name"), role.get("role_code")
                if not _valid_text(name) or not _valid_text(code, 15) or role.get("inactive"):
                    continue
                facts.append({"field": "registered_role",
                              "value": {"name": name.strip(), "role_code": code.strip()},
                              "locator": f"$.rollegrupper[{group_index}].roller[{role_index}]",
                              "reporting_period": None})
            # Source bodies are already byte-bounded; never silently truncate
            # distinct registered appointments.
        return facts, "available" if facts else ("ambiguous" if normalized else "not_available")

    if module == "locations":
        if not isinstance(body, dict) or not isinstance(body.get("_embedded"), dict):
            raise ValueError("Malformed subunits payload")
        original = body["_embedded"].get("underenheter", [])
        if not isinstance(original, list):
            raise ValueError("Malformed subunits list")
        for i, original_place in enumerate(original):
            if not isinstance(original[i], dict):
                continue
            # Normalize each raw record with its target parent while keeping
            # the original index for the source locator. Rejected records
            # must not shift a later valid record onto an earlier locator.
            normalized = normalize_locations({"_embedded": {"underenheter": [original_place]}}, org)["locations"]
            if not normalized:
                continue
            place = normalized[0]
            parent = original[i].get("overordnetEnhet")
            # Unlike a name/domain match, a published subunit must explicitly
            # point back to the exact target legal entity. Never infer parent
            # identity from a page containing an unrelated or missing parent.
            if str(parent) != org:
                continue
            orgnr = place.get("organisation_number")
            if not isinstance(orgnr, str) or re.fullmatch(r"[0-9]{9}", orgnr) is None:
                continue
            name = place.get("name")
            if not _valid_text(name):
                continue
            facts.append({"field": "registered_subunit", "value": {"orgnr": orgnr, "name": name.strip()},
                          "locator": f"$._embedded.underenheter[{i}]", "reporting_period": None})
        return facts, "available" if facts else ("ambiguous" if original else "not_available")

    if module == "financials":
        if not isinstance(body, list):
            raise ValueError("Malformed financials payload")
        # The starter normalizer intentionally processes only three records per
        # invocation. Apply it to EVERY chunk with matching raw indices.
        normalized = []
        for start in range(0, len(body), 3):
            normalized.extend(normalize_financials(body[start:start + 3])["records"])
        for i, rec in enumerate(normalized):
            original = body[i]
            if not isinstance(original, dict):
                raise ValueError("Malformed financial record")
            embedded_org = original.get("organisasjonsnummer")
            if embedded_org is not None and str(embedded_org) != org:
                continue
            # A consolidated/group report cannot be published as financials
            # of the target legal entity merely because it appears on that
            # entity's endpoint. Keep group accounts for a separately scoped
            # future parser, not the exact-entity V1 claims.
            account_type = rec.get("account_type")
            if isinstance(account_type, str) and any(
                    token in account_type.upper() for token in ("KONSERN", "CONSOLIDATED", "GROUP")):
                continue
            period = _valid_period(rec.get("period"))
            currency = rec.get("currency")
            if not period or not _valid_text(currency, 5):
                continue
            for key, path in FINANCIAL_SOURCE_PATHS.items():
                value = rec.get(key)
                if _valid_amount(value):
                    facts.append({"field": f"financial_{key}",
                                  "value": {"amount": value, "currency": currency, "period": period,
                                            "account_type": rec.get("account_type")},
                                  "locator": f"$[{i}]." + ".".join(path),
                                  "reporting_period": period})
            # Source bodies are already byte-bounded; never silently truncate
            # distinct registered appointments.
        # Conflicting filings for one identical field/period/account-type are
        # not automatically two truths. Without a verified amendment ordering,
        # withhold the conflicted field rather than invent which is current.
        groups = {}
        for f in facts:
            v=f["value"]
            key=(f["field"],f["reporting_period"],v.get("currency"),v.get("account_type"))
            groups.setdefault(key,set()).add(v["amount"])
        conflicts={key for key,values in groups.items() if len(values)>1}
        if conflicts:
            facts=[f for f in facts if (f["field"],f["reporting_period"],
                  f["value"].get("currency"),f["value"].get("account_type")) not in conflicts]
        return facts, "available" if facts else ("ambiguous" if body else "not_available")
    raise ValueError("Unknown official module")


def locator_proves_fact(module: str, org: str, body: Any, fact: dict[str, Any]) -> bool:
    """Resolve the *raw* evidence location independently of the starter normalizer.

    A synthetic JSON-pointer label, or a value merely repeated in the claim,
    is not proof. A source-backed claim must match the raw JSON object at its
    actual location with compatible entity scope and accounting period.
    """
    try:
        field, value, locator = fact["field"], fact["value"], fact["locator"]
        if module == "registry_live":
            return (field == "official_registry_name" and locator == "$.navn" and
                    isinstance(body, dict) and body.get("organisasjonsnummer") == org and
                    isinstance(body.get("navn"), str) and body["navn"].strip() == value)
        if module == "roles":
            match = re.fullmatch(r"\$\.rollegrupper\[(\d+)\]\.roller\[(\d+)\]", locator)
            if not match or not isinstance(body, dict):
                return False
            raw = body["rollegrupper"][int(match[1])]["roller"][int(match[2])]
            raw_name = raw.get("person", {}).get("navn", {})
            if not isinstance(raw_name, dict):
                return False
            name = " ".join(filter(None, (raw_name.get("fornavn"), raw_name.get("mellomnavn"),
                                           raw_name.get("etternavn"))))
            if not name:
                name = raw.get("enhet", {}).get("navn")
            return (field == "registered_role" and isinstance(value, dict) and
                    name == value.get("name") and raw.get("type", {}).get("kode") == value.get("role_code") and
                    not raw.get("avregistrert"))
        if module == "locations":
            match = re.fullmatch(r"\$\._embedded\.underenheter\[(\d+)\]", locator)
            if not match or not isinstance(body, dict):
                return False
            raw = body["_embedded"]["underenheter"][int(match[1])]
            return (field == "registered_subunit" and isinstance(value, dict) and
                    str(raw.get("overordnetEnhet")) == org and
                    str(raw.get("organisasjonsnummer")) == value.get("orgnr") and
                    isinstance(raw.get("navn"), str) and raw["navn"].strip() == value.get("name"))
        if module == "financials":
            key = field.removeprefix("financial_")
            path = FINANCIAL_SOURCE_PATHS.get(key)
            if path is None or not isinstance(body, list) or not isinstance(value, dict):
                return False
            match = re.fullmatch(r"\$\[(\d+)\]\.([a-zA-Z0-9_.]+)", locator)
            if not match or tuple(match[2].split(".")) != path:
                return False
            raw_record = body[int(match[1])]
            if (not isinstance(raw_record, dict) or
                    (raw_record.get("organisasjonsnummer") is not None and
                     str(raw_record["organisasjonsnummer"]) != org)):
                return False
            raw_amount = raw_record
            for segment in path:
                raw_amount = raw_amount[segment]
            return (type(raw_amount) in (int, float) and raw_amount == value.get("amount") and
                    raw_record.get("valuta") == value.get("currency") and
                    raw_record.get("regnskapstype") == value.get("account_type") and
                    _valid_period(raw_record.get("regnskapsperiode")) == value.get("period") == fact.get("reporting_period"))
    except (IndexError, KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return False
    return False


def source_is_complete(module: str, body: Any) -> bool:
    """Strict, shared completeness test. No inferred defaults for page metadata."""
    if module != "locations":
        return True
    if not isinstance(body, dict) or not isinstance(body.get("_embedded"), dict):
        return False
    items = body["_embedded"].get("underenheter")
    if not isinstance(items, list):
        return False
    pagination = body.get("_pagination")
    if pagination is not None:
        if not isinstance(pagination, dict) or pagination.get("complete") is not True:
            return False
        count = pagination.get("fetchedPages")
        if type(count) is not int or not 1 <= count <= MAX_LOCATION_PAGES:
            return False
        page = body.get("page")
        if not isinstance(page, dict) or page.get("number") != count-1:
            return False
        if any(type(page.get(k)) is not int for k in ("totalPages", "totalElements")):
            return False
        if page["totalPages"] != count or page["totalElements"] != len(items):
            return False
        ids = [x.get("organisasjonsnummer") for x in items if isinstance(x, dict)]
        return len(ids) == len(items) and len(set(ids)) == len(ids)
    shape = _page_shape(SourceResponse("locations", "", 200, "", body), 0)
    if shape is None:
        return False
    total, elements, _ = shape
    return total == 1 and elements == len(items)

