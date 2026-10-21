"""Strict, intentionally narrow V0.1 local validators (NOT Builderr's grader).

Structural completeness and source-backed accuracy are distinct gates.
An evidence span that echoes a claim does not by itself prove its truth; the
semantic gate must receive an independently read, checked source row.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .registry import FIELDS, REGISTRY_SOURCE, _pick, registry_facts, row_hash
from .storage import read_jsonl

AVAILABILITY = frozenset({"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"})
RUN_STATUSES = frozenset({"completed", "failed"})
REFRESH_STATES = frozenset({"no_prior_baseline", "unchanged", "changed", "source_unavailable", "identity_ambiguous"})
CHECK_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX24 = re.compile(r"^registry-[0-9a-f]{24}$")
ALLOWED_SCOPES = frozenset({"exact_legal_entity"})


def is_valid_orgnr(org: str) -> bool:
    if not isinstance(org, str) or re.fullmatch(r"[0-9]{9}", org) is None:
        return False
    if org == "000000000":
        return False
    vals = [ord(c) - ord("0") for c in org]
    checksum = 11 - sum(v * w for v, w in zip(vals[:8], CHECK_WEIGHTS)) % 11
    return (0 if checksum == 11 else checksum) == vals[8] and checksum != 10


def parse_batch(path: str | Path) -> list[str]:
    path = Path(path)
    if path.suffix == ".jsonl":
        values = read_jsonl(path, encoding="utf-8-sig")
    elif path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        values = data if isinstance(data, list) else data.get("organisation_numbers", [])
    else:
        values = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not isinstance(values, list):
        raise ValueError("Batch must be a list")
    result: list[str] = []
    for index, entry in enumerate(values):
        if isinstance(entry, dict):
            entry = entry.get("organisation_number")
        if not is_valid_orgnr(entry):
            raise ValueError(f"Invalid Norwegian organisation number at input row {index + 1}: {entry!r}")
        result.append(entry)
    if len(set(result)) != len(result):
        raise ValueError("Duplicate organisation number in input batch")
    return result


def _time(value: Any) -> bool:
    if not isinstance(value, str) or not value or not (value.endswith("Z") or "+" in value[10:] or "-" in value[10:]):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (parsed.tzinfo is not None and parsed.utcoffset() is not None and
                datetime(2000, 1, 1, tzinfo=timezone.utc) <= parsed.astimezone(timezone.utc) <=
                datetime.now(timezone.utc) + timedelta(minutes=5))
    except (ValueError, OverflowError):
        return False


def _hex(value: Any) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _number(value: Any, *, integer: bool = False, max_value: float | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)):
        return False
    if not math.isfinite(value) or value < 0:
        return False
    return max_value is None or value <= max_value


def _dict(value: Any) -> bool:
    return isinstance(value, dict)


def _member(value: Any, allowed: frozenset[str]) -> bool:
    return isinstance(value, str) and value in allowed


def _evidence_id(org: str, field: str, value: str, row_digest: str,
                 source_digest: str | None = None) -> str:
    parts = f"{org}|{field}|{value}|{row_digest}"
    if source_digest is not None:
        parts += f"|{source_digest}"
    return "registry-" + hashlib.sha256(parts.encode("utf-8")).hexdigest()[:24]


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def validate_envelopes(rows: list[dict[str, Any]], orgs: list[str], *,
                       source_rows: dict[str, dict[str, str]] | None = None,
                       source_snapshot_sha256: str | None = None,
                       previous_results: dict[str, dict[str, Any]] | None = None,
                       allow_empty_batch: bool = False) -> dict[str, Any]:
    """Local structural + evidence gate. Source rows REQUIRED for available claims.

    Historical evidence may remain without current source rows, but must be
    structurally sound and connected to an observed snapshot for this entity.
    Verifying *historical authenticity* additionally requires a trusted prior
    file digest at the loader boundary (not achieved here alone).
    """
    failures: list[str] = []
    if not rows and not allow_empty_batch:
        failures.append("empty batch is not a release-ready input")
    if not isinstance(rows, list) or [r.get("organisation_number") if _dict(r) else None for r in rows] != orgs:
        failures.append("missing, extra, duplicate or reordered organisation numbers")
    if not isinstance(rows, list):
        return {"passed": False, "count": 0, "errors": failures,
                "evidence_source_checked": False}
    if source_snapshot_sha256 is not None and not _hex(source_snapshot_sha256):
        failures.append("invalid pinned source SHA-256")
    if previous_results is not None and not isinstance(previous_results, dict):
        failures.append("invalid prior validation context")
        previous_results = {}

    for i, row in enumerate(rows):
        pre = f"{i}:"
        if not _dict(row):
            failures.append(f"{pre} not an object")
            continue
        raw_org = row.get("organisation_number")
        org = raw_org if isinstance(raw_org, str) else ""
        if not is_valid_orgnr(raw_org):
            failures.append(f"{pre} invalid organisation number")
        run = row.get("run")
        ident = row.get("legal_identity")
        availability = row.get("availability")
        claims = row.get("claims")
        evidence = row.get("evidence")
        snapshots = row.get("source_snapshots")
        refresh = row.get("refresh")
        changes = row.get("changes")
        operations = row.get("operations")
        errors = row.get("errors")
        if not _dict(run) or not _member(run.get("terminal_status"), RUN_STATUSES) or not isinstance(run.get("run_id"), str) or not run.get("run_id") or not _time(run.get("started_at")) or not _time(run.get("completed_at")):
            failures.append(f"{pre} invalid run")
        elif datetime.fromisoformat(run["completed_at"].replace("Z", "+00:00")) < datetime.fromisoformat(run["started_at"].replace("Z", "+00:00")):
            failures.append(f"{pre} run completed before start")
        if not _dict(ident) or ident.get("organisation_number") != org or not _member(ident.get("availability"), AVAILABILITY):
            failures.append(f"{pre} invalid legal identity")
        elif ident["availability"] == "available" and (not isinstance(ident.get("legal_name"), str) or not ident["legal_name"].strip()):
            failures.append(f"{pre} available identity without legal name")
        if not _dict(availability) or not availability or any(not _member(s, AVAILABILITY) for s in availability.values()):
            failures.append(f"{pre} invalid availability")
        if not isinstance(claims, list) or not isinstance(evidence, list) or not isinstance(snapshots, list) or not isinstance(changes, list) or not isinstance(errors, list):
            failures.append(f"{pre} missing or invalid collections")
            continue
        if not _dict(refresh) or not _member(refresh.get("state"), REFRESH_STATES) or not _number(refresh.get("prior_snapshots_preserved"), integer=True):
            failures.append(f"{pre} invalid refresh")
        if not _dict(operations) or any(not _number(operations.get(k), integer=(k != "third_party_cost_usd")) for k in ("requests", "runtime_ms", "third_party_cost_usd")):
            failures.append(f"{pre} invalid operations")
        if any(not _dict(e) or not isinstance(e.get("module"), str) or not isinstance(e.get("reason"), str) for e in errors):
            failures.append(f"{pre} invalid errors")
        if _dict(availability) and set(availability) != {"registry"}:
            failures.append(f"{pre} unsupported V0 availability family")
        if _dict(run) and _dict(availability) and _dict(ident):
            state = availability.get("registry")
            if run.get("terminal_status") == "failed" and (claims or state != "failed"):
                failures.append(f"{pre} failed run publishes current claims or has nonfailed registry")
            if run.get("terminal_status") == "completed" and state == "failed":
                failures.append(f"{pre} completed run has failed registry")
            if state == "available" and ident.get("availability") != "available":
                failures.append(f"{pre} available registry contradicts legal identity")
            if state != "available" and claims:
                failures.append(f"{pre} current claims published despite unavailable registry")
            if ident.get("availability") == "available" and state != "available":
                failures.append(f"{pre} available legal identity contradicts registry")

        snap_by_key: dict[tuple[str, str], dict] = {}
        for snap in snapshots:
            if not _dict(snap) or snap.get("organisation_number") != org or snap.get("source_id") != "brreg-frozen-bulk" or snap.get("source_url") != REGISTRY_SOURCE or not _hex(snap.get("row_sha256")) or not _hex(snap.get("snapshot_sha256")) or not _time(snap.get("retrieved_at")):
                failures.append(f"{pre} invalid snapshot")
                continue
            key = (snap["row_sha256"], snap["snapshot_sha256"])
            if key in snap_by_key and snap_by_key[key] != snap:
                failures.append(f"{pre} colliding snapshot identifiers")
            snap_by_key[key] = snap
            if _dict(run) and _time(run.get("completed_at")) and _datetime(snap["retrieved_at"]) > _datetime(run["completed_at"]):
                failures.append(f"{pre} snapshot dated after run completion")

        evid_by_id: dict[str, dict] = {}
        for ev in evidence:
            if not _dict(ev) or not isinstance(ev.get("id"), str):
                failures.append(f"{pre} malformed evidence")
                continue
            evid_id = ev["id"]
            if evid_id in evid_by_id:
                failures.append(f"{pre} duplicate evidence ID")
            evid_by_id[evid_id] = ev
            if not HEX24.fullmatch(evid_id) or ev.get("source_class") != "official_registry_bulk" or ev.get("source_url") != REGISTRY_SOURCE or ev.get("extraction_method") != "csv_exact_field" or ev.get("source_row_key") != org or not _hex(ev.get("content_sha256")) or not _hex(ev.get("snapshot_sha256")) or not _time(ev.get("retrieved_at")) or not isinstance(ev.get("claim_span"), str):
                failures.append(f"{pre} invalid registry evidence {evid_id}")
            span = ev.get("claim_span")
            if isinstance(span, str) and ": " in span:
                span_field, _ = span.split(": ", 1)
                aliases = next((a for f, a in FIELDS if f == span_field), ())
                if ev.get("source_field_key") not in aliases:
                    failures.append(f"{pre} evidence locator does not name registered field")
            else:
                failures.append(f"{pre} missing claim locator")
            ev_hash = ev.get("content_sha256")
            snap = (snap_by_key.get((ev_hash, ev.get("snapshot_sha256")))
                    if isinstance(ev_hash, str) and _hex(ev.get("snapshot_sha256")) else None)
            if not snap:
                failures.append(f"{pre} evidence not bound to correct snapshot")
            if _dict(run) and _time(run.get("completed_at")) and _time(ev.get("retrieved_at")) and _datetime(ev["retrieved_at"]) > _datetime(run["completed_at"]):
                failures.append(f"{pre} evidence dated after run completion")
            if isinstance(span, str) and ": " in span and isinstance(ev_hash, str):
                span_field, span_value = span.split(": ", 1)
                expected_new_id = _evidence_id(org, span_field, span_value, ev_hash, ev.get("snapshot_sha256"))
                legacy_id = _evidence_id(org, span_field, span_value, ev_hash)
                # Historical V0.1 IDs can be carried forward, but must match
                # a validated prior record, never newly asserted by the runner.
                previous_ev = (previous_results.get(org, {}).get("evidence", [])
                               if isinstance(previous_results, dict) and _dict(previous_results.get(org)) else [])
                accepted_legacy = (evid_id == legacy_id and any(p == ev for p in previous_ev))
                if evid_id != expected_new_id and not accepted_legacy:
                    failures.append(f"{pre} evidence identifier does not bind source version")

        current = source_rows.get(org) if source_rows is not None else None
        expected = dict(registry_facts(current)) if current is not None else {}
        expected_hash = row_hash(current) if current is not None else None
        available_fields: dict[str, Any] = {}
        for claim in claims:
            if not _dict(claim):
                failures.append(f"{pre} malformed claim")
                continue
            field, value = claim.get("field"), claim.get("value")
            if not isinstance(field, str):
                failures.append(f"{pre} field not string")
                continue
            if claim.get("organisation_number") != org or field not in {f for f, _ in FIELDS} or not _member(claim.get("scope"), ALLOWED_SCOPES) or claim.get("availability") != "available" or not isinstance(value, str) or not value or not _number(claim.get("confidence"), max_value=1.0) or claim.get("confidence") == 0:
                failures.append(f"{pre} invalid claim")
            if field in available_fields:
                failures.append(f"{pre} duplicate field claim {field}")
            available_fields[field] = value
            ids = claim.get("evidence_ids")
            if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or ids[0] not in evid_by_id:
                failures.append(f"{pre} invalid evidence reference {field}")
                continue
            ev = evid_by_id[ids[0]]
            if ev.get("claim_span") != f"{field}: {value}":
                failures.append(f"{pre} claim-span mismatch {field}")
            expected_id = _evidence_id(org, field, value, ev.get("content_sha256"), ev.get("snapshot_sha256"))
            if ids[0] != expected_id:
                failures.append(f"{pre} evidence ID does not bind fact content {field}")
            if current is None:
                failures.append(f"{pre} cannot verify current claim without source row {field}")
            elif (expected.get(field) != value or ev.get("content_sha256") != expected_hash or
                  ev.get("snapshot_sha256") != source_snapshot_sha256 or
                  not isinstance(ev.get("source_field_key"), str) or
                  not isinstance(current.get(ev.get("source_field_key")) if isinstance(ev.get("source_field_key"), str) else None, str) or
                  current.get(ev.get("source_field_key"), "").strip() != value):
                failures.append(f"{pre} claim not backed by current source {field}")
        if _dict(ident) and ident.get("availability") == "available":
            if available_fields.get("legal_name") != ident.get("legal_name") or not _dict(availability) or availability.get("registry") != "available":
                failures.append(f"{pre} identity does not match verified legal-name claim")
        if _dict(availability) and availability.get("registry") == "available" and not claims:
            failures.append(f"{pre} registry claims missing despite available status")
        current_ids = {eid for claim in claims if _dict(claim)
                       for eid in (claim.get("evidence_ids") if isinstance(claim.get("evidence_ids"), list) else [])
                       if isinstance(eid, str)}
        previous_evidence = {e.get("id"): e for e in previous_results.get(org, {}).get("evidence", [])
                             if _dict(e)} if isinstance(previous_results, dict) and _dict(previous_results.get(org)) else {}
        for eid, ev in evid_by_id.items():
            if eid not in current_ids and previous_evidence.get(eid) != ev:
                failures.append(f"{pre} unreferenced untrusted evidence {eid}")

        if not _dict(refresh):
            continue
        # On the first source observation no semantic changes can be known.
        if refresh.get("state") == "no_prior_baseline" and changes:
            failures.append(f"{pre} fabricated first-run changes")
        if refresh.get("state") == "unchanged" and changes:
            failures.append(f"{pre} unchanged refresh contains changes")
        if refresh.get("state") == "changed" and not changes:
            failures.append(f"{pre} changed refresh without changes")
        if isinstance(refresh.get("state"), str) and refresh.get("state") in {"changed", "unchanged"} and previous_results is None:
            failures.append(f"{pre} verified refresh requires explicit prior validation context")
        for change in changes:
            if not _dict(change) or not isinstance(change.get("field"), str) or change.get("field") not in {f for f, _ in FIELDS} or change.get("kind") not in {"added", "changed"} or change.get("new") != available_fields.get(change.get("field")) or not isinstance(change.get("new"), str):
                failures.append(f"{pre} invalid change event")
        if previous_results is not None:
            previous = previous_results.get(org)
            if previous is None or previous.get("legal_identity", {}).get("availability") != "available":
                expected_changes = []
            else:
                old_claims = {c["field"]: c["value"] for c in previous.get("claims", []) if c.get("availability") == "available"}
                expected_changes = []
                for field in sorted(available_fields):
                    if field not in old_claims:
                        expected_changes.append({"field": field, "kind": "added", "old": None, "new": available_fields[field]})
                    elif old_claims[field] != available_fields[field]:
                        expected_changes.append({"field": field, "kind": "changed", "old": old_claims[field], "new": available_fields[field]})
            # Only completed, available snapshots can emit a verified diff.
            if (_dict(availability) and availability.get("registry") == "available" and
                changes != expected_changes):
                failures.append(f"{pre} refresh changes are not derived from prior claims")
    return {"passed": not failures, "count": len(rows), "errors": failures[:80],
            "evidence_source_checked": source_rows is not None and source_snapshot_sha256 is not None}


def validate_prior_row(previous: dict[str, Any], org: str) -> None:
    """Reject structurally inconsistent prior provenance before any inheritance.

    The separately pinned prior-file digest authenticates its bytes. In fixture
    mode this verifies consistency ONLY, not third-party authenticity.
    """
    if not _dict(previous) or previous.get("organisation_number") != org:
        raise ValueError(f"untrusted previous output: entity mismatch {org}")
    mandatory = {"organisation_number", "run", "legal_identity", "claims", "evidence",
                 "availability", "source_snapshots", "refresh", "changes", "errors", "operations"}
    if not mandatory.issubset(previous):
        raise ValueError(f"untrusted previous output: required envelope metadata missing {org}")
    run = previous.get("run")
    availability = previous.get("availability")
    refresh = previous.get("refresh")
    changes = previous.get("changes")
    errors = previous.get("errors")
    operations = previous.get("operations")
    if (not _dict(run) or not _member(run.get("terminal_status"), RUN_STATUSES) or
        not isinstance(run.get("run_id"), str) or not run["run_id"] or
        not _time(run.get("started_at")) or not _time(run.get("completed_at")) or
        _datetime(run["started_at"]) > _datetime(run["completed_at"])):
        raise ValueError(f"untrusted previous output: invalid run {org}")
    if (not _dict(availability) or set(availability) != {"registry"} or
        not _member(availability["registry"], AVAILABILITY) or
        not _dict(refresh) or not _member(refresh.get("state"), REFRESH_STATES) or
        not _number(refresh.get("prior_snapshots_preserved"), integer=True) or
        not isinstance(changes, list) or not isinstance(errors, list) or
        any(not _dict(e) or not isinstance(e.get("module"), str) or
            not isinstance(e.get("reason"), str) for e in errors) or
        not _dict(operations) or any(not _number(operations.get(k), integer=k != "third_party_cost_usd")
                                          for k in ("requests", "runtime_ms", "third_party_cost_usd"))):
        raise ValueError(f"untrusted previous output: invalid status or metadata {org}")
    evidence = previous.get("evidence")
    snaps = previous.get("source_snapshots")
    claims = previous.get("claims")
    if not isinstance(evidence, list) or not isinstance(snaps, list) or not isinstance(claims, list):
        raise ValueError(f"untrusted previous output: collections missing {org}")
    by_hash = {}
    for snap in snaps:
        if (not _dict(snap) or snap.get("organisation_number") != org or
            snap.get("source_id") != "brreg-frozen-bulk" or snap.get("source_url") != REGISTRY_SOURCE or
            not _hex(snap.get("row_sha256")) or not _hex(snap.get("snapshot_sha256")) or
            not _time(snap.get("retrieved_at"))):
            raise ValueError(f"untrusted previous output: malformed snapshot {org}")
        key = (snap["row_sha256"], snap["snapshot_sha256"])
        if key in by_hash and by_hash[key] != snap:
            raise ValueError(f"untrusted previous output: snapshot collision {org}")
        if _datetime(snap["retrieved_at"]) > _datetime(run["completed_at"]):
            raise ValueError(f"untrusted previous output: snapshot after completion {org}")
        by_hash[key] = snap
    refs = {}
    for ev in evidence:
        if not _dict(ev):
            raise ValueError(f"untrusted previous output: malformed evidence {org}")
        digest, eid = ev.get("content_sha256"), ev.get("id")
        if (not isinstance(eid, str) or not HEX24.fullmatch(eid) or eid in refs or
            not _hex(digest) or not _hex(ev.get("snapshot_sha256")) or
            ev.get("source_url") != REGISTRY_SOURCE or ev.get("source_row_key") != org or
            ev.get("source_class") != "official_registry_bulk" or
            ev.get("extraction_method") != "csv_exact_field" or not _time(ev.get("retrieved_at")) or
            (digest, ev.get("snapshot_sha256")) not in by_hash):
            raise ValueError(f"untrusted previous output: evidence provenance {org}")
        span = ev.get("claim_span")
        if not isinstance(span, str) or ": " not in span:
            raise ValueError(f"untrusted previous output: evidence span {org}")
        field, value = span.split(": ", 1)
        aliases = next((a for f, a in FIELDS if f == field), ())
        expected_ids = {_evidence_id(org, field, value, digest),
                        _evidence_id(org, field, value, digest, ev.get("snapshot_sha256"))}
        if (field not in {f for f, _ in FIELDS} or ev.get("source_field_key") not in aliases or
            not value or eid not in expected_ids or
            _datetime(ev["retrieved_at"]) > _datetime(run["completed_at"])):
            raise ValueError(f"untrusted previous output: evidence ID or field {org}")
        refs[eid] = (field, value)
    seen_fields = set()
    for c in claims:
        if (not _dict(c) or c.get("organisation_number") != org or
            not isinstance(c.get("field"), str) or c.get("field") not in {f for f, _ in FIELDS} or
            c.get("field") in seen_fields or c.get("availability") != "available" or
            c.get("scope") != "exact_legal_entity" or not _number(c.get("confidence"), max_value=1) or
            c.get("confidence") == 0 or not isinstance(c.get("value"), str) or not c["value"] or
            not isinstance(c.get("evidence_ids"), list) or len(c["evidence_ids"]) != 1 or
            not isinstance(c["evidence_ids"][0], str) or
            refs.get(c["evidence_ids"][0]) != (c.get("field"), c.get("value"))):
            raise ValueError(f"untrusted previous output: claim not bound to evidence {org}")
        seen_fields.add(c["field"])
    identity = previous.get("legal_identity")
    if not _dict(identity) or identity.get("organisation_number") != org or not _member(identity.get("availability"), AVAILABILITY):
        raise ValueError(f"untrusted previous output: legal identity {org}")
    if identity.get("availability") == "available" and dict((c["field"], c["value"]) for c in claims).get("legal_name") != identity.get("legal_name"):
        raise ValueError(f"untrusted previous output: legal name {org}")
    if ((availability["registry"] == "available") != (identity["availability"] == "available") or
        (availability["registry"] != "available" and claims) or
        (availability["registry"] == "available" and not claims) or
        (run["terminal_status"] == "failed" and (claims or availability["registry"] != "failed")) or
        (run["terminal_status"] == "completed" and availability["registry"] == "failed")):
        raise ValueError(f"untrusted previous output: inconsistent state {org}")
    if (refresh["state"] == "no_prior_baseline" and changes or
        refresh["state"] == "unchanged" and changes or
        refresh["state"] == "changed" and not changes):
        raise ValueError(f"untrusted previous output: inconsistent refresh {org}")
    field_values = {c["field"]: c["value"] for c in claims}
    for ch in changes:
        if (not _dict(ch) or not isinstance(ch.get("field"), str) or
            ch["field"] not in {f for f, _ in FIELDS} or ch.get("kind") not in {"changed", "added"} or
            ch.get("new") != field_values.get(ch["field"])):
            raise ValueError(f"untrusted previous output: invalid change {org}")
