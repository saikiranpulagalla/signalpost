"""Current-data identity bootstrap for explicitly authorised BRREG pilots.

This module deliberately does not read or emulate the frozen benchmark
registry.  A candidate becomes a publishable legal identity only after the
live entity endpoint confirms the requested organisation number.
"""
from __future__ import annotations

from typing import Any, Callable

from .evidence import evidence, utc_now
from .http import FetchResult
from .official import BRREG_ENTITY, accounting_obligation_assessment, normalize_entity


def live_registry_projection(normalized: dict[str, Any]) -> dict[str, Any]:
    """Single source of truth for every live baseline field used by claims."""
    industry = normalized.get("industry") or {}
    address = normalized.get("business_address") or {}
    return {
        **{field: normalized.get(field) for field in (
            "name", "legal_form", "employees", "bankrupt", "liquidating", "website",
            "business_address", "postal_address", "latest_submitted_accounts")},
        "industry_code": industry.get("kode") if isinstance(industry, dict) else None,
        "industry_label": industry.get("beskrivelse") if isinstance(industry, dict) else None,
        "municipality": address.get("kommune") if isinstance(address, dict) else None,
        "municipality_number": address.get("kommunenummer") if isinstance(address, dict) else None,
    }


def candidate_profiles(organisation_numbers: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Create unverified candidates; no identity fact is claimed at this point."""
    now = utc_now()
    profiles = []
    for org in organisation_numbers:
        profiles.append({
            "organisation_number": org,
            "name": None,
            "website": None,
            "evidence": {
                "registry": evidence("registry", "not_fetched", "official_registry_live",
                                     BRREG_ENTITY.format(org=org), retrieved_at=now,
                                     note="Current live identity verification has not run", source_row_key=org),
                "accounting_obligation": evidence("accounting_obligation", "blocked", "official_rule_interpretation",
                                                    "https://www.brreg.no/", retrieved_at=now,
                                                    note="Current live identity verification has not run", source_row_key=org),
            },
        })
    return profiles, {
        "identity_mode": "live_verified",
        "registry_snapshot_sha256": None,
        "registry_rows_scanned": 0,
        "requested": len(organisation_numbers),
        "selected": 0,
        "missing": len(organisation_numbers),
        "note": "Candidates are Builderr-universe members, not frozen-registry identities.",
    }


def resolve_live_identity(profile: dict[str, Any], fetcher: Callable[[str], FetchResult]) -> tuple[dict[str, Any], FetchResult, bool]:
    """Resolve one candidate and reject any response not bound to its requested ID."""
    org = profile["organisation_number"]
    result = fetcher(BRREG_ENTITY.format(org=org))
    source_url = result.url or BRREG_ENTITY.format(org=org)
    if result.status == 200 and not result.error:
        normalized = normalize_entity(result.body)
        if normalized.get("organisation_number") != org:
            record = evidence("registry", "source_error", "official_registry_live", source_url,
                              value=None, note="Live identity response organisation number does not match request",
                              content_sha256=result.content_sha256, retrieved_at=result.retrieved_at,
                              effective_at=result.effective_at, source_row_key=org)
            profile["evidence"]["registry"] = record
            profile["evidence"]["registry_live"] = dict(record, field="registry_live")
            profile["evidence"]["accounting_obligation"] = evidence(
                "accounting_obligation", "blocked", "official_rule_interpretation", "https://www.brreg.no/",
                note="Live identity verification failed", retrieved_at=result.retrieved_at, source_row_key=org)
            return profile, result, False
        profile.update(live_registry_projection(normalized))
        record = evidence("registry", "available", "official_registry_live", source_url, value=result.body,
                          content_sha256=result.content_sha256, retrieved_at=result.retrieved_at,
                          effective_at=result.effective_at, source_row_key=org)
        profile["evidence"]["registry"] = record
        profile["evidence"]["registry_live"] = evidence("registry_live", "available", "official_registry_live", source_url,
                                                           value=normalized, content_sha256=result.content_sha256,
                                                           retrieved_at=result.retrieved_at, effective_at=result.effective_at,
                                                           source_row_key=org)
        profile["evidence"]["accounting_obligation"] = accounting_obligation_assessment(profile)
        return profile, result, True
    status = "not_fetched" if result.error in {"source request budget exhausted", "source family budget exhausted", "execution deadline reserve reached", "network policy offline", "network policy rejected URL", "source service cooldown", "transport service stopped"} else "not_found" if result.status in {404, 410} else "source_error"
    note = result.error or ("Live entity was not found" if status == "not_found" else "Live identity request failed")
    profile["evidence"]["registry"] = evidence("registry", status, "official_registry_live", source_url,
                                                  note=note, content_sha256=result.content_sha256,
                                                  retrieved_at=result.retrieved_at, effective_at=result.effective_at,
                                                  source_row_key=org)
    profile["evidence"]["registry_live"] = evidence("registry_live", status, "official_registry_live", source_url,
                                                       note=note, content_sha256=result.content_sha256,
                                                       retrieved_at=result.retrieved_at, effective_at=result.effective_at,
                                                       source_row_key=org)
    profile["evidence"]["accounting_obligation"] = evidence(
        "accounting_obligation", "blocked", "official_rule_interpretation", "https://www.brreg.no/",
        note="Live identity unavailable", retrieved_at=result.retrieved_at, source_row_key=org)
    return profile, result, False
