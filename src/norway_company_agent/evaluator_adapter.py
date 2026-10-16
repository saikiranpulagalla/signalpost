"""Explicit boundary between internal research results and evaluator output."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from .batch import canonical_organisation_number


class OfficialSchemaUnavailableError(RuntimeError):
    pass


STARTER_AVAILABILITY = frozenset({"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"})
_INTERNAL_AVAILABILITY = {
    "available": "available", "not_found": "not_available", "blocked": "blocked",
    "not_applicable": "not_applicable", "ambiguous": "ambiguous", "source_error": "failed",
}


def starter_availability(internal_status: str) -> str:
    """Map a documented evidence meaning, never workflow completion.

    ``not_fetched`` is deliberately not mapped.  The starter contract defines
    ambiguity as uncertain attribution, not as a catch-all for unattempted or
    budget-exhausted sources.  A caller must retain that internal state and
    fail closed rather than publish a semantically invented availability.
    """
    try:
        return _INTERNAL_AVAILABILITY[internal_status]
    except KeyError as exc:
        raise ValueError(f"Unknown internal availability state: {internal_status!r}") from exc


def _evidence_id(org: str, field: str, record: dict[str, Any]) -> str:
    material = "|".join((org, field, str(record.get("source_url") or ""), str(record.get("content_sha256") or "")))
    return "ev-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def adapt_terminal_envelope(envelope: dict[str, Any], *, observation_key: bytes | None = None) -> dict[str, Any]:
    """Produce only fields documented by this repository's starter contract.

    This is deliberately *starter_compatible*, not a certification of
    Builderr's unpublished evaluator schema.  Claims lacking an available
    evidence record with a source URL, retrieval time, and digest are omitted
    rather than being weakly attributed.
    """
    profile = envelope.get("profile")
    if not isinstance(profile, dict):
        raise ValueError("Internal envelope lacks profile")
    org = canonical_organisation_number(envelope.get("organisation_number"))
    if org != profile.get("organisation_number"):
        raise ValueError("Internal envelope/profile organisation mismatch")
    from .profile_integrity import verify_history
    verify_history(profile, observation_key)
    source_records = profile.get("evidence")
    research = profile.get("research_result")
    if not isinstance(source_records, dict) or not isinstance(research, dict):
        raise ValueError("Internal envelope lacks evidence-linked research result")
    evidence_rows: list[dict[str, Any]] = []
    evidence_ids: dict[str, str] = {}
    for field, record in sorted(source_records.items()):
        if not isinstance(record, dict) or record.get("status") != "available":
            continue
        if not all(isinstance(record.get(key), str) and record[key] for key in ("source_url", "retrieved_at", "content_sha256")):
            continue
        identifier = _evidence_id(org, field, record)
        evidence_ids[field] = identifier
        evidence_rows.append({
            "id": identifier, "source_url": record["source_url"],
            "source_class": record.get("source_class") or record.get("source_type") or "unknown",
            "retrieved_at": record["retrieved_at"], "content_sha256": record["content_sha256"],
            "claim_span": field,
        })
        row = evidence_rows[-1]
        if record.get("extraction_method"):
            row["extraction_method"] = record["extraction_method"]
        else:
            row["extraction_method_status"] = "not retained in historical observation; no method invented"
        locators = [{"field": c.get("field"), "reporting_period": c.get("reporting_period"), **c["source_locator"]}
                    for c in research.get("claims", []) if field in c.get("evidence_fields", []) and isinstance(c.get("source_locator"), dict)]
        if locators:
            row["claim_span"] = json.dumps(locators, ensure_ascii=False, sort_keys=True)
        if record.get("completeness"):
            row["completeness"] = record["completeness"]
    claims = []
    for claim in research.get("claims", []):
        if not isinstance(claim, dict):
            continue
        refs = [evidence_ids[field] for field in claim.get("evidence_fields", []) if field in evidence_ids]
        if refs:
            # The starter contract deliberately leaves ``value`` open-ended.
            # Keep the accounting context inside that documented value rather
            # than adding an undocumented top-level claim key.  Without this,
            # a financial value loses the period and legal-entity scope that
            # made it safe to publish in the internal result.
            value = claim.get("value")
            if (
                isinstance(value, dict)
                and isinstance(claim.get("reporting_period"), dict)
                and claim.get("scope")
            ):
                value = dict(value)
                value["reporting_period"] = claim["reporting_period"]
                value["scope"] = claim["scope"]
            claims.append({"field": claim.get("field"), "value": value, "availability": "available",
                           "confidence": 1.0, "evidence_ids": refs})
    errors = []
    for field, record in sorted(source_records.items()):
        if isinstance(record, dict) and record.get("status") != "available":
            errors.append(f"{field}: {starter_availability(str(record.get('status')))}")
            errors.append(json.dumps({"source_family": field, "source_outcome": starter_availability(str(record.get("status"))),
                **{k: record.get(k) for k in ("source_url", "retrieved_at", "content_sha256", "http_status", "note", "extraction_method")}}, sort_keys=True))
        elif isinstance(record, dict) and record.get("completeness") == "partial":
            errors.append(json.dumps({"source_family": field, "warning": "partial source collection; remaining pages not checked",
                **{k: record.get(k) for k in ("source_url", "retrieved_at", "content_sha256", "note")}}, sort_keys=True))
    metric = profile.get("run_metrics") if isinstance(profile.get("run_metrics"), dict) else {}
    started = envelope.get("started_at")
    completed = envelope.get("completed_at")
    runtime_ms = 0
    try:
        runtime_ms = max(0, round((datetime.fromisoformat(str(completed).replace("Z", "+00:00")) - datetime.fromisoformat(str(started).replace("Z", "+00:00"))).total_seconds() * 1000))
    except ValueError:
        pass
    return {
        "organisation_number": org,
        "run": {"run_id": envelope.get("run_id"), "started_at": started, "completed_at": completed,
                "terminal_status": "failed" if envelope.get("state") == "submission_error" else "completed"},
        "claims": claims, "evidence": evidence_rows, "changes": profile.get("changes", []) if isinstance(profile.get("changes"), list) else [],
        "errors": errors,
        "operations": {"requests": int(metric.get("requests") or 0), "runtime_ms": runtime_ms, "third_party_cost_usd": 0},
    }


def validate_starter_envelopes(envelopes: list[dict[str, Any]], expected_organisations: list[str]) -> dict[str, Any]:
    """Strict validation of the repository's documented starter envelope."""
    errors: list[str] = []
    if [item.get("organisation_number") for item in envelopes] != expected_organisations:
        errors.append("ordered input membership mismatch")
    keys = {"organisation_number", "run", "claims", "evidence", "changes", "errors", "operations"}
    for row in envelopes:
        if set(row) != keys:
            errors.append("unexpected or missing starter envelope fields")
            continue
        try:
            canonical_organisation_number(row["organisation_number"])
        except ValueError:
            errors.append("invalid organisation number")
        run = row.get("run")
        if not isinstance(run, dict) or run.get("terminal_status") not in {"completed", "failed"}:
            errors.append("invalid terminal run state")
        source_ids = {item.get("id") for item in row.get("evidence", []) if isinstance(item, dict)}
        for item in row.get("evidence", []):
            if not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key] for key in ("id", "source_url", "source_class", "retrieved_at", "content_sha256", "claim_span")):
                errors.append("invalid evidence record")
        for claim in row.get("claims", []):
            if not isinstance(claim, dict) or claim.get("availability") not in STARTER_AVAILABILITY:
                errors.append("invalid claim availability")
                continue
            if claim.get("availability") == "available" and (not claim.get("evidence_ids") or not set(claim["evidence_ids"]).issubset(source_ids)):
                errors.append("available claim lacks valid evidence reference")
        operations = row.get("operations")
        if not isinstance(operations, dict) or any(not isinstance(operations.get(key), (int, float)) or operations[key] < 0 for key in ("requests", "runtime_ms", "third_party_cost_usd")):
            errors.append("invalid operations")
    return {"passed": not errors, "errors": errors[:80]}


def adapt_research_result(result: dict[str, Any], *, target: str = "internal_v1") -> dict[str, Any]:
    """Return the internal contract or fail closed for an undocumented one."""
    if target == "internal_v1":
        return result
    if target in {"builderr", "builderr_official"}:
        raise OfficialSchemaUnavailableError(
            "Builderr field-level output schema and output-discovery contract have not been supplied"
        )
    if target == "starter_compatible":
        raise ValueError("starter_compatible conversion requires an internal terminal envelope and evidence records")
    raise ValueError(f"Unknown evaluator target: {target}")
