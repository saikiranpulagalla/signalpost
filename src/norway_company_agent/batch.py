from __future__ import annotations

import hashlib
import json
import copy
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .evidence import evidence, utc_now
from .official import accounting_obligation_assessment
from .sampling import iter_bulk


TERMINAL_STATES = {
    "complete",
    "not_applicable",
    "not_found",
    "blocked_policy",
    "blocked_robots",
    "source_error",
    "budget_exhausted",
    "submission_error",
}

EVIDENCE_STATUSES = {"available", "not_found", "not_applicable", "not_fetched", "source_error", "blocked"}
_ORG_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def canonical_organisation_number(value: Any) -> str:
    """Accept only explicit human formatting and return a Mod-11-valid ID."""
    if not isinstance(value, str):
        raise ValueError(f"Invalid Norwegian organisation number: {value!r}")
    candidate = value.strip()
    candidate = re.sub(r"^NO[ \u00a0-]*", "", candidate, flags=re.I)
    candidate = re.sub(r"[ \u00a0-]*MVA$", "", candidate, flags=re.I)
    if not re.fullmatch(r"[0-9 \u00a0-]+", candidate):
        raise ValueError(f"Invalid Norwegian organisation number: {value!r}")
    org = re.sub(r"[ \u00a0-]", "", candidate)
    if len(org) != 9 or org == "000000000":
        raise ValueError(f"Invalid Norwegian organisation number: {value!r}")
    checksum = 11 - sum(int(digit) * weight for digit, weight in zip(org[:8], _ORG_WEIGHTS)) % 11
    if checksum == 10 or (0 if checksum == 11 else checksum) != int(org[8]):
        raise ValueError(f"Invalid Norwegian organisation number: {value!r}")
    return org


def read_organisation_inputs(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    values: list[Any]
    if source.suffix == ".json":
        body = json.loads(text)
        values = body if isinstance(body, list) else body.get("organisation_numbers", [])
    elif source.suffix == ".jsonl":
        values = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        values = [line.strip() for line in text.splitlines() if line.strip()]
    records = []
    for value in values:
        org = canonical_organisation_number(value.get("organisation_number") if isinstance(value, dict) else value)
        record = {"organisation_number": org}
        if isinstance(value, dict):
            for key in ("evaluation_split", "sample_slice"):
                if value.get(key) is not None:
                    record[key] = value[key]
        records.append(record)
    orgs = [record["organisation_number"] for record in records]
    if len(orgs) != len(set(orgs)):
        raise ValueError("Organisation-number input contains duplicates")
    return records


def read_organisation_numbers(path: str | Path) -> list[str]:
    return [record["organisation_number"] for record in read_organisation_inputs(path)]


def profiles_from_bulk(path: str | Path, organisation_numbers: Iterable[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requested = list(organisation_numbers)
    wanted = set(requested)
    snapshot_bytes = Path(path).read_bytes()
    snapshot_sha256 = hashlib.sha256(snapshot_bytes).hexdigest()
    retrieved_at = utc_now()
    found: dict[str, dict[str, Any]] = {}
    scanned = 0
    for profile in iter_bulk(snapshot_bytes):
        scanned += 1
        org = profile["organisation_number"]
        if org not in wanted:
            continue
        if org in found:
            raise ValueError(f"Duplicate registry identity for requested organisation number: {org}")
        raw = profile.pop("raw", {})
        profile["evidence"] = {
            "registry": evidence(
                "registry",
                "available",
                "official_registry_bulk",
                "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv",
                value=raw,
                retrieved_at=retrieved_at,
                content_sha256=snapshot_sha256,
                source_row_key=org,
            ),
            "accounting_obligation": accounting_obligation_assessment(profile),
        }
        found[org] = profile
    profiles = []
    for org in requested:
        if org in found:
            profiles.append(found[org])
            continue
        profiles.append({
            "organisation_number": org,
            "name": None,
            "website": None,
            "evidence": {
                "registry": evidence("registry", "source_error", "official_registry_bulk",
                                     "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv",
                                     note="Requested organisation number absent from frozen registry snapshot",
                                     retrieved_at=retrieved_at, content_sha256=snapshot_sha256,
                                     source_row_key=org),
                "accounting_obligation": evidence("accounting_obligation", "blocked", "official_rule_interpretation",
                                                   "https://www.brreg.no/", note="Registry identity unavailable",
                                                   retrieved_at=retrieved_at, source_row_key=org),
            },
        })
    return profiles, {
        "registry_snapshot_sha256": snapshot_sha256,
        "registry_rows_scanned": scanned,
        "requested": len(requested),
        "selected": len(found), "missing": len(requested) - len(found),
    }


def evidence_terminal_state(record: dict[str, Any] | None) -> str:
    if not record:
        return "submission_error"
    status = record.get("status")
    if status == "available":
        return "complete"
    if status == "not_applicable":
        return "not_applicable"
    if status == "not_found":
        return "not_found"
    if status == "blocked":
        note = str(record.get("note") or "").casefold()
        return "blocked_robots" if "robot" in note else "blocked_policy"
    if status == "source_error":
        return "source_error"
    return "submission_error"


def terminal_envelope(
    profile: dict[str, Any],
    *,
    run_id: str,
    modules: Iterable[str],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    # Defense in depth: final competition envelope must never carry historical
    # website publication from weaker runners or resume files. Keep the
    # official source claims untouched; quarantine untrusted web records.
    sanitized_profile = copy.deepcopy(profile)
    if isinstance(sanitized_profile.get("evidence"), dict):
        for web_key in ("website", "website_discovered", "website_discovery"):
            rec = sanitized_profile["evidence"].get(web_key)
            if not isinstance(rec, dict):
                continue
            rec = dict(rec)
            # Do not expose old scraped page claims as profile fields, even
            # when their parent module is marked blocked. Preserve only an
            # optional digest for diagnostic lookup, not the scraped content.
            original = rec.get("value")
            old_digest = original.get("content_sha256") if isinstance(original, dict) else None
            val = {"website_claims_published":0, "publication_disabled":True}
            if isinstance(old_digest, str) and len(old_digest)==64:
                val["quarantined_content_sha256"] = old_digest
            rec.update(status="blocked", value=val,
                       note="V2a.1 final-envelope website publication safety interlock")
            sanitized_profile["evidence"][web_key] = rec
    module_states = {}
    for module in modules:
        record = sanitized_profile.get("evidence", {}).get(module)
        module_states[module] = {
            "state": evidence_terminal_state(record),
            "retry_count": int((record or {}).get("retry_count") or 0),
            "final_timestamp": (record or {}).get("retrieved_at") or completed_at,
        }
    if any(item["state"] == "submission_error" for item in module_states.values()):
        entity_state = "submission_error"
    elif any(item["state"] == "source_error" for item in module_states.values()):
        entity_state = "source_error"
    else:
        entity_state = "complete"
    return {
        "run_id": run_id,
        "organisation_number": sanitized_profile["organisation_number"],
        "state": entity_state,
        "started_at": started_at,
        "completed_at": completed_at,
        "modules": module_states,
        "profile": sanitized_profile,
    }


def _valid_timestamp(value: Any, *, upper_bound: str | None = None) -> bool:
    try:
        instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset() is None:
            return False
        if upper_bound:
            return instant <= datetime.fromisoformat(upper_bound.replace("Z", "+00:00"))
        return instant <= datetime.now(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return False


def validate_envelopes(envelopes: list[dict[str, Any]], expected: int | Iterable[str]) -> dict[str, Any]:
    """Validate the V2 evaluator envelope; iterable input enables strict membership checks."""
    expected_orgs = list(expected) if not isinstance(expected, int) else None
    expected_count = expected if isinstance(expected, int) else len(expected_orgs)
    orgs = [item.get("organisation_number") for item in envelopes]
    invalid_states = [
        {"organisation_number": item.get("organisation_number"), "state": state.get("state")}
        for item in envelopes
        for state in item.get("modules", {}).values()
        if state.get("state") not in TERMINAL_STATES
    ]
    semantic_errors: list[dict[str, Any]] = []
    if expected_orgs is not None and orgs != expected_orgs:
        semantic_errors.append({"error": "output organisation numbers do not exactly match ordered input"})
    for envelope in envelopes:
        org = envelope.get("organisation_number")
        if not isinstance(org, str):
            semantic_errors.append({"organisation_number": org, "error": "invalid organisation number"})
            continue
        try:
            canonical_organisation_number(org)
        except ValueError:
            semantic_errors.append({"organisation_number": org, "error": "invalid organisation number"})
        if not isinstance(envelope.get("run_id"), str) or not envelope["run_id"].strip():
            semantic_errors.append({"organisation_number": org, "error": "missing run id"})
        if not _valid_timestamp(envelope.get("started_at")) or not _valid_timestamp(envelope.get("completed_at"), upper_bound=None):
            semantic_errors.append({"organisation_number": org, "error": "invalid timestamps"})
        elif datetime.fromisoformat(envelope["completed_at"].replace("Z", "+00:00")) < datetime.fromisoformat(envelope["started_at"].replace("Z", "+00:00")):
            semantic_errors.append({"organisation_number": org, "error": "completion precedes start"})
        modules = envelope.get("modules")
        evidence_records = envelope.get("profile", {}).get("evidence") if isinstance(envelope.get("profile"), dict) else None
        if not isinstance(modules, dict) or not modules or not isinstance(evidence_records, dict):
            semantic_errors.append({"organisation_number": org, "error": "missing modules or profile evidence"})
            continue
        for module, state in modules.items():
            record = evidence_records.get(module)
            if not isinstance(state, dict) or not isinstance(record, dict):
                semantic_errors.append({"organisation_number": org, "error": f"missing evidence for module {module}"})
                continue
            if record.get("field") != module or record.get("status") not in EVIDENCE_STATUSES:
                semantic_errors.append({"organisation_number": org, "error": f"invalid evidence state for {module}"})
            if not isinstance(record.get("source_url"), str) or not record["source_url"].startswith(("https://", "http://")):
                semantic_errors.append({"organisation_number": org, "error": f"invalid source URL for {module}"})
            if not _valid_timestamp(record.get("retrieved_at")):
                semantic_errors.append({"organisation_number": org, "error": f"invalid retrieval time for {module}"})
            digest = record.get("content_sha256")
            if digest is not None and (not isinstance(digest, str) or not _HEX64.fullmatch(digest)):
                semantic_errors.append({"organisation_number": org, "error": f"invalid source hash for {module}"})
            if state.get("state") != evidence_terminal_state(record):
                semantic_errors.append({"organisation_number": org, "error": f"module state mismatch for {module}"})
    checks = {
        "exact_expected_count": len(envelopes) == expected_count,
        "unique_organisation_numbers": len(orgs) == len(set(orgs)),
        "all_entity_states_terminal": all(item.get("state") in TERMINAL_STATES for item in envelopes),
        "all_module_states_terminal": not invalid_states,
        "semantic_integrity": not semantic_errors,
        "zero_silent_drops": len(envelopes) == expected_count and len(orgs) == len(set(orgs)),
    }
    return {"passed": all(checks.values()), "checks": checks, "invalid_states": invalid_states,
            "semantic_errors": semantic_errors[:80]}


def profile_complete_for_modules(profile: dict[str, Any], modules: Iterable[str]) -> bool:
    records = profile.get("evidence", {})
    return all(module in records and records[module].get("status") != "not_fetched" for module in modules)
