"""Pinned, typed V1 official-source refresh ledger (no untrusted history merges).

History is *not* independently authenticated by a claim-spanning hash: the
operator must provide the SHA-256 of an independently trusted earlier output.
Previous facts remain historical observations, never current publications.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .sources import canonical_bytes, endpoint
from v0_kernel.storage import read_jsonl_bytes

MODULES = ("registry_live", "roles", "locations", "financials")
MAX_PRIOR_BYTES = 256 * 1024 * 1024


def _official_ids(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["id"]: e for e in row["evidence"]
            if isinstance(e, dict) and e.get("source_class") == "official_brreg_api"}


def _current_claims(row: dict[str, Any], module: str) -> dict[bytes, dict[str, Any]]:
    evidence = _official_ids(row)
    result: dict[bytes, dict[str, Any]] = {}
    occurrences: dict[bytes, int] = {}
    for c in row["claims"]:
        if not isinstance(c, dict) or not isinstance(c.get("evidence_ids"), list):
            continue
        if any(evidence.get(eid, {}).get("module") == module for eid in c["evidence_ids"]):
            semantic = canonical_bytes([c.get("field"), c.get("value"), c.get("reporting_period")])
            occurrence = occurrences.get(semantic, 0)
            occurrences[semantic] = occurrence + 1
            item = dict(c, _source_occurrence=occurrence)
            key = canonical_bytes([c.get("field"), c.get("value"), c.get("reporting_period"),
                                   occurrence if module == "roles" else 0])
            result[key] = item
    return result


def load_prior(path: Path, expected_sha: str, orgs: list[str]) -> list[dict[str, Any]]:
    if not isinstance(expected_sha, str) or re.fullmatch(r"[a-f0-9]{64}", expected_sha) is None:
        raise ValueError("Refresh requires a pinned 64-character lowercase SHA-256")
    if path.stat().st_size > MAX_PRIOR_BYTES:
        raise ValueError("Prior output exceeds safe size limit")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError("Previous output does not match trusted pinned digest")
    try:
        rows = read_jsonl_bytes(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed pinned previous JSONL") from exc
    prior_ids = [r.get("organisation_number") if isinstance(r, dict) else None for r in rows]
    if any(not isinstance(o, str) or len(o) != 9 for o in prior_ids) or len(prior_ids) != len(set(prior_ids)):
        raise ValueError("Prior identity batch contains invalid or duplicate organisation numbers")
    for org, row in zip(prior_ids, rows):
        if not isinstance(row.get("claims"), list) or not isinstance(row.get("evidence"), list) or \
           not isinstance(row.get("source_snapshots"), list) or not isinstance(row.get("availability"), dict) or \
           not isinstance(row.get("official_modules"), dict):
            raise ValueError("Malformed prior result collections")
        evidence = _official_ids(row)
        if len(evidence) != sum(isinstance(e, dict) and e.get("source_class") == "official_brreg_api"
                                for e in row["evidence"]):
            raise ValueError("Duplicate or malformed prior official evidence IDs")
        for eid, ev in evidence.items():
            module = ev.get("module")
            if (module not in MODULES or ev.get("source_url") != endpoint(module, org) or
                    ev.get("source_row_key") != org or not isinstance(eid, str) or
                    not eid.startswith("official-")):
                raise ValueError("Previous evidence has invalid source identity")
        for c in row["claims"]:
            if not isinstance(c, dict):
                raise ValueError("Malformed prior claim")
            ids = c.get("evidence_ids")
            if not isinstance(ids, list):
                raise ValueError("Malformed prior claim evidence references")
            for eid in ids:
                if isinstance(eid, str) and eid.startswith("official-") and eid not in evidence:
                    raise ValueError("Previous official claim has missing evidence")
        for snap in row["source_snapshots"]:
            if not isinstance(snap, dict):
                raise ValueError("Malformed previous source snapshot")
            if snap.get("source_id") in MODULES:
                if (snap.get("organisation_number") != org or
                        snap.get("source_url") != endpoint(snap["source_id"], org) or
                        not isinstance(snap.get("snapshot_sha256"), str) or
                        re.fullmatch(r"[0-9a-f]{64}", snap["snapshot_sha256"]) is None):
                    raise ValueError("Previous official snapshot identity or hash invalid")
        for e in row.get("historical_official_evidence", []):
            if not isinstance(e, dict) or e.get("source_class") != "official_brreg_api" or \
               e.get("source_row_key") != org or e.get("module") not in MODULES or \
               e.get("source_url") != endpoint(e["module"], org):
                raise ValueError("Malformed historical official evidence")
        for s in row.get("historical_official_snapshots", []):
            if not isinstance(s, dict) or s.get("source_id") not in MODULES or \
               s.get("organisation_number") != org or \
               s.get("source_url") != endpoint(s["source_id"], org):
                raise ValueError("Malformed historical official snapshot")
    return rows


def prior_by_org(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """An independently SHA-pinned prior batch is keyed by immutable orgnr, never row order."""
    mapping = {r["organisation_number"]: r for r in rows}
    if len(mapping) != len(rows):
        raise ValueError("Duplicate identities in pinned previous batch")
    return mapping



def _stored_claims(row: dict[str, Any], module: str) -> list[dict[str, Any]]:
    # Store a stable occurrence counter for otherwise indistinguishable
    # registered appointments. Pure source list reordering is not a change.
    return [dict(c) for c in _current_claims(row, module).values()]

def _last_success(prior: dict[str, Any], module: str) -> dict[str, Any] | None:
    """Return the last successful observation across blocked/failed refreshes."""
    own = prior.get("last_verified_official", {})
    if isinstance(own, dict) and module in own and isinstance(own[module], dict):
        return own[module]
    if prior.get("availability", {}).get(module) in ("available", "not_available") and \
            isinstance(prior.get("official_modules", {}).get(module, {}).get("semantic_sha256"), str):
        return {"claims": _stored_claims(prior, module),
                "module_state": prior["availability"][module],
                "source_snapshot_sha256": prior["official_modules"][module].get("content_sha256"),
                "retrieved_at": prior["official_modules"][module].get("retrieved_at")}
    return None


def _claim_key(claim: dict[str, Any], evidence: dict[str, dict[str, Any]] | None = None) -> bytes:
    # Role appointments with the same name/role can still be distinct source records.
    count = claim.get("_source_occurrence", 0) if claim.get("field") == "registered_role" else 0
    return canonical_bytes([claim.get("field"), claim.get("value"), claim.get("reporting_period"), count])


def refresh_view(current: dict[str, Any], prior: dict[str, Any] | None,
                 prior_sha: str | None) -> dict[str, Any]:
    """Carry forward immutable source versions and last-known-good per-module state."""
    if prior is None:
        last_good = {}
        for module in MODULES:
            if current.get("availability", {}).get(module) in ("available", "not_available") and \
                    current.get("official_modules", {}).get(module, {}).get("semantic_sha256"):
                last_good[module] = {
                    "claims": _stored_claims(current, module),
                    "module_state": current["availability"][module],
                    "source_snapshot_sha256": current["official_modules"][module].get("content_sha256"),
                    "retrieved_at": current["official_modules"][module].get("retrieved_at")}
        return {"official_refresh": {"state": "no_prior_baseline", "previous_sha256": None,
                 "prior_evidence_preserved": 0, "prior_snapshots_preserved": 0},
                "official_changes": [], "historical_official_evidence": [],
                "historical_official_snapshots": [], "last_verified_official": last_good,
                "official_fetch_observations": []}

    present_eid = {e.get("id") for e in current.get("evidence", []) if isinstance(e, dict)}
    old_evidence = {}
    for entry in prior.get("evidence", []) + prior.get("historical_official_evidence", []):
        if (isinstance(entry, dict) and entry.get("source_class") == "official_brreg_api" and
                isinstance(entry.get("id"), str) and entry["id"] not in present_eid):
            old_evidence.setdefault(entry["id"], entry)
    historical_evidence = [old_evidence[k] for k in sorted(old_evidence)]

    # Content versions are distinct from fetch observations. Never duplicate a
    # full snapshot merely because the same content was retrieved on another day.
    old_snapshots = {}
    for entry in prior.get("source_snapshots", []) + prior.get("historical_official_snapshots", []):
        if isinstance(entry, dict) and entry.get("source_id") in MODULES:
            key = (entry.get("source_id"), entry.get("snapshot_sha256"), entry.get("semantic_sha256"))
            old_snapshots.setdefault(key, entry)
    historical_snapshots = [old_snapshots[k] for k in sorted(old_snapshots)]

    observations = list(prior.get("official_fetch_observations", []))
    # Older formats did not store explicit observation history. Migrate their last
    # successful source observations once, without losing the timestamps.
    if "official_fetch_observations" not in prior:
        for entry in prior.get("source_snapshots", []):
            if isinstance(entry, dict) and entry.get("source_id") in MODULES:
                observations.append({"module": entry["source_id"], "source_sha256": entry.get("snapshot_sha256"),
                                     "retrieved_at": entry.get("retrieved_at")})
    for entry in current.get("source_snapshots", []):
        if isinstance(entry, dict) and entry.get("source_id") in MODULES:
            observations.append({"module": entry["source_id"], "source_sha256": entry.get("snapshot_sha256"),
                                 "retrieved_at": entry.get("retrieved_at")})
    # Same observed timestamp/content is one observation. Distinct retrievals
    # remain auditable, using compact records instead of repeated full snapshots.
    unique_observations = {}
    for entry in observations:
        if isinstance(entry, dict) and entry.get("module") in MODULES:
            key = (entry.get("module"), entry.get("source_sha256"), entry.get("retrieved_at"))
            unique_observations.setdefault(key, entry)
    observations = [unique_observations[k] for k in sorted(unique_observations)]

    events = []
    any_unknown = False
    last_good = {}
    current_evidence = _official_ids(current)
    for module in MODULES:
        earlier = _last_success(prior, module)
        current_state = current.get("availability", {}).get(module)
        source = current.get("official_modules", {}).get(module, {})
        fresh_good = (current_state in ("available", "not_available") and
                      isinstance(source.get("semantic_sha256"), str))
        if not fresh_good:
            any_unknown = True
            if earlier is not None:
                last_good[module] = earlier
            continue
        current_claims = _stored_claims(current, module)
        last_good[module] = {"claims": current_claims, "module_state": current_state,
                             "source_snapshot_sha256": source.get("content_sha256"),
                             "retrieved_at": source.get("retrieved_at")}
        if earlier is None:
            continue
        before = {_claim_key(c): c for c in earlier.get("claims", [])}
        after = {_claim_key(c, current_evidence): c for c in current_claims}
        # Backwards compatibility: older snapshots stored role claims without
        # a source-record locator. Recover locators from pinned prior evidence.
        if prior.get("availability", {}).get(module) in ("available", "not_available") and \
                "last_verified_official" not in prior:
            before = {_claim_key(c): c for c in _stored_claims(prior, module)}
        for key in sorted(set(after) - set(before)):
            c = after[key]
            events.append({"module": module, "kind": "added", "field": c["field"],
                           "value": c["value"], "reporting_period": c.get("reporting_period")})
        for key in sorted(set(before) - set(after)):
            c = before[key]
            events.append({"module": module, "kind": "removed", "field": c["field"],
                           "value": c["value"], "reporting_period": c.get("reporting_period")})
    state = ("partial_with_changes" if any_unknown and events else
             "source_unavailable" if any_unknown else "changed" if events else "unchanged")
    return {"official_refresh": {"state": state, "previous_sha256": prior_sha,
                 "prior_evidence_preserved": len(historical_evidence),
                 "prior_snapshots_preserved": len(historical_snapshots)},
            "official_changes": events, "historical_official_evidence": historical_evidence,
            "historical_official_snapshots": historical_snapshots,
            "last_verified_official": last_good,
            "official_fetch_observations": observations}
