"""Internal, evidence-linked company research result.

This is deliberately separate from the undisclosed Builderr terminal schema.
It turns the runner's verified source records into claim-shaped data and a
deterministic summary without altering V0/V1 evidence or publication rules.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from .synthesis import build_synthesis


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _claim(org: str, field: str, value: Any, evidence_field: str, record: dict[str, Any], *, period: Any = None, locator: str | dict[str, Any] | None = None) -> dict[str, Any]:
    digest = hashlib.sha256(f"{org}|{field}|{_canonical(value)}|{evidence_field}|{period or ''}".encode("utf-8")).hexdigest()[:24]
    result = {
        "id": f"claim-{digest}", "field": field, "value": value,
        "scope": "exact_legal_entity", "evidence_fields": [evidence_field],
        "source_url": record.get("source_url"), "source_sha256": record.get("content_sha256"),
        "retrieved_at": record.get("retrieved_at"), "reporting_period": period,
    }
    if locator and record.get("extraction_method"):
        result["source_locator"] = locator if isinstance(locator, dict) else {"representation": "normalized observation, not a raw-source quote", "pointer": locator}
    return result


def _available(profile: dict[str, Any], field: str) -> dict[str, Any] | None:
    record = profile.get("evidence", {}).get(field)
    return record if isinstance(record, dict) and record.get("status") == "available" else None


def build_research_result(profile: dict[str, Any]) -> dict[str, Any]:
    """Normalize only claims supported by an available exact-company record."""
    org = str(profile.get("organisation_number") or "")
    claims: list[dict[str, Any]] = []
    registry = _available(profile, "registry")
    if registry:
        for field, value in (
            ("legal_name", profile.get("name")), ("legal_form", profile.get("legal_form")),
            ("industry", {"code": profile.get("industry_code"), "description": profile.get("industry_label")} if profile.get("industry_code") or profile.get("industry_label") else None),
            ("registered_municipality", profile.get("municipality")),
            ("employee_count", profile.get("employees")),
            ("legal_status", {key: profile[key] for key in ("bankrupt", "liquidating") if isinstance(profile.get(key), bool)}),
        ):
            if value not in (None, "", {}):
                claims.append(_claim(org, field, value, "registry", registry))
        raw = registry.get("value")
        if registry.get("source_type") == "official_registry_bulk" and isinstance(raw, dict):
            from .sampling import frozen_registry_details
            live_value = (_available(profile, "registry_live") or {}).get("value") or {}
            for field, value, columns in frozen_registry_details(raw):
                # Prefer a separately checked current observation when present;
                # the frozen row/evidence remains available for inspection.
                if field == "registered_address" and isinstance(live_value, dict) and (live_value.get("business_address") or live_value.get("postal_address")):
                    continue
                if field == "registered_website" and isinstance(live_value, dict) and live_value.get("website"):
                    continue
                if field in {"registered_activity", "registered_purpose"} and isinstance(live_value, dict) and live_value.get(field) not in (None, "", []):
                    continue  # Current declaration once; frozen observation remains inspectable.
                claims.append(_claim(org, field, value, "registry", registry, locator={
                    "representation": "frozen CSV columns, normalization applied", "columns": columns}))
    accounting = _available(profile, "accounting_obligation")
    if accounting and accounting.get("value"):
        claims.append(_claim(org, "accounting_obligation", accounting["value"], "accounting_obligation", accounting))
    live = _available(profile, "registry_live")
    if live and isinstance(live.get("value"), dict):
        value = live["value"]
        for field, item in (("registered_address", value.get("business_address") or value.get("postal_address")),
                            ("registered_website", value.get("website")),
                            ("registered_legal_status", {key: value[key] for key in ("bankrupt", "liquidating") if isinstance(value.get(key), bool)})):
            if item not in (None, "", {}):
                claims.append(_claim(org, field, item, "registry_live", live))
        if value.get("declaration_projection_version") == 1:
            for field, source in (("registered_activity", "aktivitet"), ("registered_purpose", "vedtektsfestetFormaal")):
                if value.get(field) not in (None, "", []):
                    claims.append(_claim(org, field, value[field], "registry_live", live, locator={
                        "representation": "registered JSON declaration; not observed operations", "pointer": "/" + source}))
    roles = _available(profile, "roles")
    if roles and isinstance(roles.get("value"), dict):
        for index, role in enumerate(roles["value"].get("roles", [])):
            if isinstance(role, dict) and (role.get("role") or role.get("role_code")):
                claims.append(_claim(org, "registered_role", role, "roles", roles, period=role.get("last_changed"), locator=f"/value/roles/{index}"))
    locations = _available(profile, "locations")
    if locations and isinstance(locations.get("value"), dict):
        for index, location in enumerate(locations["value"].get("locations", [])):
            if isinstance(location, dict) and location.get("organisation_number") and location.get("parent_organisation_number") == org:
                claims.append(_claim(org, "registered_subunit", location, "locations", locations, locator=f"/value/locations/{index}"))
    financials = _available(profile, "financials")
    if financials and isinstance(financials.get("value"), dict):
        for index, record in enumerate(financials["value"].get("records", [])):
            if not isinstance(record, dict) or not record.get("period") or record.get("scope") != "exact_legal_entity":
                continue
            period = record["period"]
            for metric in ("revenue", "operating_result", "profit_before_tax", "annual_result", "assets", "equity", "debt"):
                if record.get(metric) is not None:
                    claims.append(_claim(org, f"financial_{metric}", {"amount": record[metric], "currency": record.get("currency"), "account_type": record.get("account_type")}, "financials", financials, period=period, locator=f"/value/records/{index}/{metric}"))
    group = _available(profile, "group")
    if group and isinstance(group.get("value"), dict):
        for index, relation in enumerate(group["value"].get("relations", [])):
            if isinstance(relation, dict):
                claims.append(_claim(org, "corporate_relationship", relation, "group", group, locator=f"/value/relations/{index}"))
    claims.sort(key=lambda item: (item["field"], _canonical(item["value"]), str(item.get("reporting_period") or "")))
    availability = {field: record.get("status") for field, record in sorted(profile.get("evidence", {}).items()) if isinstance(record, dict)}
    version = 3 if any(c["field"] in {"registered_activity", "registered_purpose"} for c in claims) else 2
    if live and (live.get("value") or {}).get("declaration_projection_version") == 1:
        version = 4
    return {"schema": "signalpost.internal.research-result/v1", "normalization_version": version, "organisation_number": org,
            "claims": claims, "availability": availability}


def attach_research_result(profile: dict[str, Any]) -> dict[str, Any]:
    result = build_research_result(profile)
    profile["research_result"] = result
    profile["synthesis"] = build_synthesis(result, profile.get("changes", []))
    return profile

