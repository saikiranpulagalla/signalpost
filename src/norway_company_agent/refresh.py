from __future__ import annotations

from typing import Any
import hashlib
import json


TRACKED_FIELDS: dict[str, tuple[str, ...]] = {
    "registry.name": ("name",),
    "registry.legal_form": ("legal_form",),
    "registry.employees": ("employees",),
    "registry.municipality": ("municipality",),
    "registry.website": ("website",),
    "registry.latest_submitted_accounts": ("latest_submitted_accounts",),
    "registry.registered_activity": ("evidence", "registry", "value", "aktivitet"),
    "registry.registered_purpose": ("evidence", "registry", "value", "vedtektsfestetFormaal"),
    "registry_live.registered_activity": ("evidence", "registry_live", "value", "registered_activity"),
    "registry_live.registered_purpose": ("evidence", "registry_live", "value", "registered_purpose"),
    "registry_live.registered_name_history": ("evidence", "registry_live", "value", "registered_name_history"),
    "financials.records": ("evidence", "financials", "value", "records"),
    "financial_history.years": ("evidence", "financial_history", "value", "years"),
    "roles.roles": ("evidence", "roles", "value", "roles"),
    "locations.locations": ("evidence", "locations", "value", "locations"),
    "website.title": ("evidence", "website", "value", "title"),
    "website.description": ("evidence", "website", "value", "description"),
    "website.social_links": ("evidence", "website", "value", "social_links"),
}


def _read(value: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _evidence_for(profile: dict[str, Any], field: str) -> dict[str, Any]:
    module = field.split(".", 1)[0]
    records = profile.get("evidence", {})
    if module == "registry":
        if field in {"registry.registered_activity", "registry.registered_purpose"}:
            return records.get("registry", {})
        return records.get("registry_live") or records.get("registry", {})
    return records.get(module, {})


def diff_profile(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    old_org = previous.get("organisation_number")
    new_org = current.get("organisation_number")
    if not old_org or old_org != new_org:
        raise ValueError("Refresh comparison requires the same exact organisation number")
    changes = []
    for field, path in TRACKED_FIELDS.items():
        old_value = _read(previous, path)
        new_value = _read(current, path)
        if old_value == new_value:
            continue
        record = _evidence_for(current, field)
        previous_record = _evidence_for(previous, field)
        changes.append({
            "organisation_number": new_org,
            "field": field,
            "old_value": old_value,
            "new_value": new_value,
            "source_url": record.get("source_url"),
            "retrieved_at": record.get("retrieved_at"),
            "effective_at": record.get("effective_at") or record.get("as_of"),
            "source_class": record.get("source_class") or record.get("source_type"),
            "old_content_sha256": previous_record.get("content_sha256"),
            "new_content_sha256": record.get("content_sha256"),
            "status": record.get("status"),
        })
    return changes


def diff_datasets(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    old_by_org = {row["organisation_number"]: row for row in previous}
    new_by_org = {row["organisation_number"]: row for row in current}
    if set(old_by_org) != set(new_by_org):
        raise ValueError("Refresh datasets must have identical organisation-number membership")
    return [
        change
        for org in sorted(old_by_org)
        for change in diff_profile(old_by_org[org], new_by_org[org])
    ]


def supported_change(org: str, field: str, old: dict, new: dict) -> dict | None:
    """Reconstruct an event from verified records, not from stored event values."""
    if field not in TRACKED_FIELDS or old.get("status") != "available" or new.get("status") != "available":
        return None
    module, name = field.split(".", 1)
    def value(record):
        data = record.get("value")
        if module == "registry":
            if not isinstance(data, dict):
                raise ValueError("Historical registry identity mismatch")
            if record.get("source_type") == "official_registry_bulk":
                from .sampling import normalize_row, frozen_registry_details
                projected = normalize_row(data)
                projected.update({key: value for key, value, _ in frozen_registry_details(data)})
            else:
                from .official import normalize_entity
                from .live_bootstrap import live_registry_projection
                projected = live_registry_projection(normalize_entity(data))
                projected["organisation_number"] = data.get("organisasjonsnummer")
            if projected.get("organisation_number") != org:
                raise ValueError("Historical registry identity mismatch")
            return projected.get(name)
        return data.get(name) if isinstance(data, dict) else None
    a, b = value(old), value(new)
    if a is None or b is None or a == b:
        return None
    change = {"organisation_number": org, "field": field, "old_value": a, "new_value": b,
        "source_url": new.get("source_url"), "retrieved_at": new.get("retrieved_at"),
        "effective_at": new.get("effective_at") or new.get("as_of"),
        "source_class": new.get("source_class") or new.get("source_type"),
        "old_content_sha256": old.get("content_sha256"), "new_content_sha256": new.get("content_sha256"),
        "status": new.get("status")}
    change["id"] = "change-" + hashlib.sha256(json.dumps({key: change[key] for key in (
        "organisation_number", "field", "old_value", "new_value", "old_content_sha256", "new_content_sha256")}, sort_keys=True).encode()).hexdigest()[:24]
    def periods(record):
        return {json.dumps(r.get("period"), sort_keys=True) for r in record.get("value", {}).get("records", [])}
    change["classification"] = ("historical_progression" if module == "financials" and periods(old) != periods(new)
                                else "material_change")
    change["previous_evidence"], change["current_evidence"] = old, new
    return change
