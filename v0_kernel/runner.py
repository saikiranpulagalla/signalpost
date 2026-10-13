"""Bounded, zero-network V0 baseline. Failures do not silently drop identities."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .contracts import parse_batch, validate_envelopes, validate_prior_row
from .registry import REGISTRY_SOURCE, FIELDS, read_selected_rows, registry_facts, row_hash, snapshot_checksum, pinned_private_copy
from .storage import atomic_json, atomic_jsonl, read_jsonl


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def placeholder(org: str, run_id: str, started_at: str, message: str = "not_processed") -> dict[str, Any]:
    return {
        "organisation_number": org,
        "run": {"run_id": run_id, "started_at": started_at, "completed_at": started_at, "terminal_status": "failed"},
        "legal_identity": {"organisation_number": org, "availability": "failed", "legal_name": None},
        "claims": [], "evidence": [], "availability": {"registry": "failed"},
        "source_snapshots": [], "refresh": {"state": "no_prior_baseline", "prior_snapshots_preserved": 0},
        "changes": [], "errors": [{"module": "registry", "reason": message}],
        "operations": {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0},
    }


def keep_prior_evidence(result: dict[str, Any], previous: dict[str, Any] | None) -> None:
    """Preserve historical evidence even if today's frozen source is unavailable."""
    if not previous:
        return
    seen = {e.get("id") for e in result["evidence"]}
    result["evidence"].extend(e for e in previous.get("evidence", []) if e.get("id") not in seen)
    snaps = {(s.get("row_sha256"), s.get("snapshot_sha256")) for s in result["source_snapshots"]}
    result["source_snapshots"].extend(s for s in previous.get("source_snapshots", [])
                                       if (s.get("row_sha256"), s.get("snapshot_sha256")) not in snaps)
    result["refresh"]["prior_snapshots_preserved"] = len(previous.get("source_snapshots", []))


def canonical_changes(previous: dict[str, Any] | None, current_claims: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    if previous is None or previous.get("legal_identity", {}).get("availability") != "available":
        return [], "no_prior_baseline"
    prior = {c["field"]: c["value"] for c in previous.get("claims", []) if c.get("availability") == "available"}
    latest = {c["field"]: c["value"] for c in current_claims if c.get("availability") == "available"}
    changes = []
    for key in sorted(set(prior) | set(latest)):
        if key not in prior:
            changes.append({"field": key, "kind": "added", "old": None, "new": latest[key]})
        elif key not in latest:
            # Absence from today's incomplete source is NOT a verified deletion.
            # A removed field must first be explicitly verified; withhold it in V0.
            continue
        elif prior[key] != latest[key]:
            changes.append({"field": key, "kind": "changed", "old": prior[key], "new": latest[key]})
    return changes, "changed" if changes else "unchanged"


def _build(org: str, row: dict[str, str] | None, run_id: str, started: str, retrieved_at: str,
           bulk_sha: str, previous: dict[str, Any] | None, elapsed_ms: int) -> dict[str, Any]:
    result = placeholder(org, run_id, started)
    result["run"].update(terminal_status="completed", completed_at=retrieved_at)
    result["operations"]["runtime_ms"] = elapsed_ms
    if row is None:
        result["legal_identity"]["availability"] = "not_available"
        result["availability"]["registry"] = "not_available"
        result["errors"] = [{"module": "registry", "reason": "organisation number absent from frozen snapshot"}]
        keep_prior_evidence(result, previous)
        result["refresh"]["state"] = "source_unavailable"
        return result
    digest = row_hash(row)
    facts = registry_facts(row)
    legal_name = next((v for f, v in facts if f == "legal_name"), None)
    if not legal_name:
        result["legal_identity"] = {"organisation_number": org, "legal_name": None, "availability": "ambiguous"}
        result["availability"]["registry"] = "ambiguous"
        result["errors"] = [{"module": "registry", "reason": "missing legal name; facts withheld"}]
        keep_prior_evidence(result, previous)
        result["refresh"]["state"] = "identity_ambiguous"
        return result
    evidences = []
    claims = []
    for field, value in facts:
        # Stable ID across replays; source row hash changes only when registry data changes.
        # Observation identity is source-version-aware; semantic fact identity
        # remains the field/value pair used by canonical_changes().
        stable_id = hashlib.sha256(f"{org}|{field}|{value}|{digest}|{bulk_sha}".encode("utf-8")).hexdigest()[:24]
        evidence_id = f"registry-{stable_id}"
        source_key = next((key for name, aliases in FIELDS if name == field
                           for key in aliases if row.get(key) is not None and
                           isinstance(row.get(key), str) and row[key].strip() == value), None)
        claims.append({"organisation_number": org, "field": field, "value": value,
                       "availability": "available", "confidence": 1.0,
                       "scope": "exact_legal_entity", "evidence_ids": [evidence_id]})
        evidences.append({"id": evidence_id, "source_url": REGISTRY_SOURCE,
                          "source_class": "official_registry_bulk", "retrieved_at": retrieved_at,
                          "content_sha256": digest, "snapshot_sha256": bulk_sha,
                          "source_row_key": org, "extraction_method": "csv_exact_field",
                          "source_field_key": source_key,
                          "claim_span": f"{field}: {value}"})
    result["claims"] = claims
    result["evidence"] = evidences
    result["legal_identity"] = {"organisation_number": org,
                                "legal_name": legal_name, "availability": "available" if legal_name else "ambiguous"}
    result["availability"] = {"registry": "available"}
    result["errors"] = [] if legal_name else [{"module": "registry", "reason": "missing legal name"}]
    result["source_snapshots"] = [{"source_id": "brreg-frozen-bulk", "source_url": REGISTRY_SOURCE,
                                   "snapshot_sha256": bulk_sha, "row_sha256": digest,
                                   "retrieved_at": retrieved_at, "organisation_number": org}]
    changes, refresh_state = canonical_changes(previous, claims)
    result["changes"] = changes
    result["refresh"]["state"] = refresh_state
    if previous:
        # Historical evidence is preserved verbatim with original retrieval timestamp.
        prior_evidence = {e["id"]: e for e in previous.get("evidence", []) if e.get("id")}
        current_ids = {e["id"] for e in evidences}
        result["evidence"].extend(prior_evidence[k] for k in sorted(prior_evidence) if k not in current_ids)
        prior_snapshots = previous.get("source_snapshots", [])
        snapshots = {(s.get("row_sha256"), s.get("snapshot_sha256")): s
                     for s in result["source_snapshots"]}
        for old in prior_snapshots:
            key = (old.get("row_sha256"), old.get("snapshot_sha256"))
            if key not in snapshots:
                snapshots[key] = old
        result["source_snapshots"] = [snapshots[k] for k in sorted(snapshots)]
        result["refresh"]["prior_snapshots_preserved"] = len(prior_snapshots)
    return result


def run(*, organisations: Path, bulk: Path, output: Path, report: Path, run_id: str,
        expected_count: int | None = None, previous_output: Path | None = None,
        checkpoint_every: int = 25, stop_after: int | None = None,
        on_checkpoint: Callable[[list[dict[str, Any]]], None] | None = None,
        fixture_mode: bool = False, expected_bulk_sha256: str | None = None,
        expected_previous_sha256: str | None = None) -> dict[str, Any]:
    if checkpoint_every < 1:
        raise ValueError("checkpoint_every must be >= 1")
    orgs = parse_batch(organisations)
    if not orgs and expected_count != 0:
        raise ValueError("Empty batch requires explicit expected_count=0 (test only)")
    if expected_count is not None and len(orgs) != expected_count:
        raise ValueError(f"Expected {expected_count} identities, received {len(orgs)}")
    # Never allow a generated output to overwrite ANY input/source artifact.
    protected = {organisations.resolve(), bulk.resolve()}
    if previous_output is not None:
        protected.add(previous_output.resolve())
    if output.resolve() in protected or report.resolve() in protected or output.resolve() == report.resolve():
        raise ValueError("output/report paths must differ from each other and all inputs")
    if not fixture_mode and (not expected_bulk_sha256 or len(expected_bulk_sha256) != 64):
        raise ValueError("Non-fixture mode requires an independently pinned --expected-bulk-sha256")
    if previous_output is not None and not fixture_mode and not expected_previous_sha256:
        raise ValueError("Non-fixture refresh requires a previously pinned output SHA-256")
    if previous_output is not None and not previous_output.exists():
        raise ValueError("Previous output was specified but does not exist")
    started = utc_now()
    start_clock = time.monotonic()
    result = [placeholder(org, run_id, started) for org in orgs]
    # First commit makes the OUTPUT file complete and parseable before any work.
    atomic_jsonl(output, result)
    if on_checkpoint:
        on_checkpoint(result)
    prior: dict[str, dict[str, Any]] = {}
    processed = 0
    checked = 0
    failure: str | None = None
    bulk_sha = ""
    try:
        if previous_output is not None:
            with pinned_private_copy(previous_output, expected_previous_sha256) as (prior_copy, _):
                for prev in read_jsonl(prior_copy):
                    if not isinstance(prev, dict):
                        raise ValueError("untrusted previous output: row must be object")
                    org = prev.get("organisation_number")
                    if not isinstance(org, str) or org not in orgs:
                        raise ValueError("untrusted previous output: unknown organisation number")
                    if org in prior:
                        raise ValueError("duplicate organisation number in prior output")
                    validate_prior_row(prev, org)
                    prior[org] = prev
            for item in result:
                old = prior.get(item["organisation_number"])
                if old:
                    keep_prior_evidence(item, old)
                    item["refresh"]["state"] = "source_unavailable"
            # Preserve the entire validated prior history even if the current
            # registry source is unreadable or the process crashes next.
            atomic_jsonl(output, result)
        # Authentication and parsing use a *single* private snapshot, NOT two
        # separate reads of the caller-controlled file path.
        with pinned_private_copy(bulk, expected_bulk_sha256) as (bulk_copy, verified_digest):
            bulk_sha = verified_digest
            rows = read_selected_rows(bulk_copy, set(orgs))
        now = utc_now()
        for index, org in enumerate(orgs):
            item_start = time.monotonic()
            result[index] = _build(org, rows.get(org), run_id, started, now,
                                   bulk_sha, prior.get(org), 0)
            result[index]["operations"]["runtime_ms"] = int((time.monotonic() - item_start) * 1000)
            processed += 1
            if processed % checkpoint_every == 0 or processed == len(orgs):
                atomic_jsonl(output, result)
                if on_checkpoint:
                    on_checkpoint(result)
            if stop_after is not None and processed >= stop_after:
                raise InterruptedError("simulated stop after checkpoint (test only)")
    except Exception as exc:
        failure = f"{type(exc).__name__}: {str(exc)[:300]}"
        # Commit latest in-memory successful work. All remaining items stay 'failed'.
        atomic_jsonl(output, result)
        if on_checkpoint:
            on_checkpoint(result)
    validation = validate_envelopes(result, orgs,
                                    source_rows=rows if 'rows' in locals() else None,
                                    source_snapshot_sha256=bulk_sha if bulk_sha else None,
                                    previous_results=prior,
                                    allow_empty_batch=expected_count == 0 and fixture_mode)
    elapsed = int((time.monotonic() - start_clock) * 1000)
    runtimes = sorted(r["operations"]["runtime_ms"] for r in result)
    p50 = runtimes[len(runtimes) // 2] if runtimes else None
    p95 = runtimes[min(len(runtimes) - 1, int(len(runtimes) * 0.95))] if runtimes else None
    completed = (failure is None and processed == len(orgs) and
                 not any(r["availability"].get("registry") == "failed" for r in result))
    report_body = {
        "run_id": run_id, "started_at": started, "completed_at": utc_now(),
        "requested": len(orgs), "processed": processed, "validated_outputs": validation,
        "registry_available": sum(1 for r in result if r["availability"].get("registry") == "available"),
        "registry_missing": sum(1 for r in result if r["availability"].get("registry") == "not_available"),
        "failed": sum(1 for r in result if r["availability"].get("registry") == "failed"),
        "network_requests": 0, "declared_external_cost_usd": 0,
        "wall_clock_ms": elapsed, "company_runtime_p50_ms": p50,
        "company_runtime_p95_ms": p95, "source_snapshot_sha256": bulk_sha,
        "interruption_or_error": failure, "v0_scope": "offline official frozen-bulk baseline only",
        "source_authenticity": ("fixture_unverified" if fixture_mode else
                                "externally_pinned_sha256" if not failure and bulk_sha else
                                "pinned_or_source_invalid"),
        "execution_success": completed and validation["passed"],
        "release_gate_passed": (completed and validation["passed"] and not fixture_mode
                                and expected_count != 0 and bool(bulk_sha)
                                and any(r["availability"].get("registry") == "available" for r in result)),
        "structural_gate_passed": validation["passed"],
        "evidence_gate_passed": validation["passed"] and validation["evidence_source_checked"] and bool(bulk_sha),
        "coverage_observed": (sum(r["availability"].get("registry") == "available" for r in result) / len(orgs)
                              if orgs else None),
        "official_schema_verified": False, "official_budget_verified": False,
        "official_release_ready": False,
        "official_score": None,
    }
    atomic_json(report, report_body)
    return report_body
