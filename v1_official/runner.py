"""V1a official BRREG integration with V0.2 as a pinned identity trust root.

Only official allowlisted endpoints; fixed phases; fail closed; no web crawling.
Fixture mode is strictly local and never marks official scoring readiness.
"""
from __future__ import annotations

import hashlib
import json
import time
import threading
import queue
import math
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from v0_kernel.contracts import parse_batch, validate_envelopes as validate_v0_envelopes
from v0_kernel.registry import pinned_private_copy, read_selected_rows
from v0_kernel.runner import _build, placeholder
from v0_kernel.storage import atomic_json, atomic_jsonl, read_jsonl
from .sources import (MODULE_URLS, SourceResponse, canonical_bytes, endpoint, extract,
                      fixture_fetch, live_fetch, sha, utc_now, verified_payload, locator_proves_fact,
                      source_is_complete, origin_for_fact, origin_observation_for_fact,
                      assemble_location_pages, MAX_COMBINED_BYTES, _page_shape)
from .refresh import load_prior, refresh_view, prior_by_org
from norway_company_agent.network_policy import NetworkPolicy

MODULES = ("registry_live", "roles", "locations", "financials")


def _v0_identity_baseline(orgs: list[str], bulk: Path, run_id: str, started: str,
                          expected_bulk_sha256: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build and validate the same pinned V0 identity rows without temp outputs.

    V1 previously invoked the whole V0 CLI internally, including three durable
    writes to a disposable temporary directory.  V1 never consumed that
    temporary artifact: it only needed the validated immutable identity rows.
    Keeping the parsing, private-copy, row builder, and V0 validator here
    preserves that trust boundary while reserving short V1 budgets for source
    finalization rather than temporary checkpoint I/O.
    """
    with pinned_private_copy(bulk, expected_bulk_sha256) as (bulk_copy, snapshot_sha256):
        source_rows = read_selected_rows(bulk_copy, set(orgs))
    retrieved_at = utc_now()
    rows = [_build(org, source_rows.get(org), run_id, started, retrieved_at,
                   snapshot_sha256, None, 0) for org in orgs]
    validation = validate_v0_envelopes(rows, orgs, source_rows=source_rows,
                                       source_snapshot_sha256=snapshot_sha256)
    report = {"execution_success": bool(validation.get("passed")),
              "validated_outputs": validation,
              "source_snapshot_sha256": snapshot_sha256}
    if not report["execution_success"]:
        raise ValueError("V0 pinned registry baseline did not pass")
    return rows, report


def _hash_claim(org: str, module: str, field: str, value: Any, semantic_hash: str, locator: str) -> str:
    raw = canonical_bytes([org, module, field, value, semantic_hash, locator])
    return "official-" + hashlib.sha256(raw).hexdigest()[:32]


def _failure_state(response: SourceResponse) -> str:
    if response.status in (404, 410):
        return "not_available"
    if response.status in (401, 403, 429):
        return "blocked"
    return "failed"


def _enhance(base: dict[str, Any], module: str, response: SourceResponse) -> None:
    org = base["organisation_number"]
    base.setdefault("official_modules", {})
    if response.status != 200:
        state = _failure_state(response)
        base["availability"][module] = state
        base["official_modules"][module] = {"state": state, "reason": response.error or f"HTTP {response.status}"}
        if state == "failed":
            base["errors"].append({"module": module, "reason": response.error or "official fetch failed"})
        return
    try:
        # Payload must fit the bounded source budget and be strict JSON.
        encoded = canonical_bytes(response.body)
        max_size = MAX_COMBINED_BYTES if module == "locations" and response.pages else 2 * 1024 * 1024
        if len(encoded) > max_size or not verified_payload(response):
            raise ValueError("oversized, tampered, or unauthenticated source payload")
        if (module == "locations" and not response.pages and isinstance(response.body, dict) and
                "page" in response.body and _page_shape(response, 0) is None):
            raise ValueError("malformed standalone pagination metadata")
        candidates, state = extract(module, org, response.body,
                                    frozen_legal_name=base.get("legal_identity", {}).get("legal_name"))
        if any(not locator_proves_fact(module, org, response.body, fact) for fact in candidates):
            raise ValueError("raw source locator does not corroborate normalized claim")
        if state not in ("available", "not_available", "ambiguous"):
            raise ValueError("unexpected normalized source status")
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        base["availability"][module] = "failed"
        base["official_modules"][module] = {"state": "failed", "reason": f"normalizer: {type(exc).__name__}"}
        base["errors"].append({"module": module, "reason": f"normalizer: {type(exc).__name__}"})
        return
    base["availability"][module] = state
    semantic_hash = hashlib.sha256(encoded).hexdigest()
    base["official_modules"][module] = {"state": state, "source_url": response.url,
                                       "retrieved_at": response.retrieved_at,
                                       "content_sha256": response.content_sha256,
                                       "semantic_sha256": semantic_hash,
                                       "source_complete": source_is_complete(module, response.body),
                                       "identity_proof": "endpoint_exact_org" if module == "financials" else "source_anchored",
                                       "collection_state": "complete" if source_is_complete(module, response.body) else "partial",
                                       "continuation_state": response.continuation_state,
                                       "continuation_http_status": response.continuation_http_status,
                                       "continuation_reason": response.continuation_reason}
    if response.pages:
        base["official_modules"][module]["page_provenance"] = [
            {"url": p.url, "content_sha256": p.content_sha256,
             "semantic_sha256": sha(p.body), "retrieved_at": p.retrieved_at}
            for p in response.pages]
    base["source_snapshots"].append({"source_id": module, "source_url": response.url,
                                    "organisation_number": org, "retrieved_at": response.retrieved_at,
                                    "snapshot_sha256": response.content_sha256,
                                    "semantic_sha256": semantic_hash})
    if state != "available":
        return
    seen = set()
    for fact in candidates:
        key = canonical_bytes([fact["field"], fact["value"], fact["reporting_period"],
                               fact["locator"] if module == "roles" else None])
        if key in seen:
            continue
        seen.add(key)
        evidence_id = _hash_claim(org, module, fact["field"], fact["value"], semantic_hash, fact["locator"])
        source_url, source_digest, local_locator, observed_at = origin_observation_for_fact(response, fact["locator"])
        ev = {"id": evidence_id, "source_url": source_url,
              "source_class": "official_brreg_api", "module": module,
              "retrieved_at": observed_at, "content_sha256": source_digest,
              "semantic_sha256": semantic_hash, "source_row_key": org,
              "extraction_method": "starter_normalizer_verified", "locator": fact["locator"],
              "claim_span": canonical_bytes([fact["field"], fact["value"]]).decode("utf-8")}
        if response.pages:
            ev["source_page_locator"] = local_locator
        base["evidence"].append(ev)
        base["claims"].append({"organisation_number": org, "field": fact["field"],
                               "value": fact["value"], "availability": "available",
                               "confidence": 1.0, "scope": "exact_legal_entity",
                               "reporting_period": fact["reporting_period"], "evidence_ids": [evidence_id]})


def _validate_v1_impl(rows: list[dict[str, Any]], orgs: list[str], bases: list[dict[str, Any]],
                      source_responses: dict[tuple[str, str], SourceResponse],
                      previous_rows: list[dict[str, Any]] | None = None,
                      previous_sha256: str | None = None) -> dict[str, Any]:
    """Validate V1 extension against fetched source bodies and V0 identity base.

    This is *not* a formal Builderr v2 schema validator. All claims must be
    independently reproducible by the original-source normalizer from the same
    in-memory body that was hashed at fetch time.
    """
    errors: list[str] = []
    if [r.get("organisation_number") for r in rows] != orgs:
        errors.append("input-to-output membership/order mismatch")
    if len(rows) != len(bases):
        errors.append("base row count mismatch")
    previous_map = prior_by_org(previous_rows) if previous_rows is not None else {}
    for i, (row, base) in enumerate(zip(rows, bases)):
        org = row["organisation_number"]
        ops = row.get("operations")
        if not isinstance(ops, dict) or any(type(ops.get(k)) not in (int, float) or
                not math.isfinite(ops[k]) or ops[k] < 0 for k in
                ("requests", "runtime_ms", "third_party_cost_usd", "official_request_count", "official_fetch_ms")):
            errors.append(f"{org}: malformed or negative operation counters")
        if not isinstance(row.get("errors"), list) or any(not isinstance(e, dict) or
                not isinstance(e.get("module"), str) or not isinstance(e.get("reason"), str)
                for e in row.get("errors", [])):
            errors.append(f"{org}: malformed error entries")
        if row.get("run", {}).get("terminal_status") != "completed":
            errors.append(f"{org}: final research envelope not completed")
        for module in MODULES:
            state = row.get("availability", {}).get(module)
            if row.get("official_modules", {}).get(module, {}).get("state") != state:
                errors.append(f"{org}: module {module} contradicts availability")
            if state in ("failed", "blocked", "ambiguous", "not_available", "not_applicable"):
                if any(e.get("module") == module for e in row.get("evidence", []) if isinstance(e, dict) and
                       e.get("source_class") == "official_brreg_api"):
                    errors.append(f"{org}: nonavailable module has published evidence")
        expected_refresh = refresh_view(row, previous_map.get(org),
                                        previous_sha256 if org in previous_map else None)
        for key, expected in expected_refresh.items():
            if row.get(key) != expected:
                errors.append(f"{org}: official refresh/history differs from pinned prior: {key}")
        for k in ("legal_identity", "refresh", "changes"):
            if row.get(k) != base.get(k):
                errors.append(f"{org}: V0 immutable section modified: {k}")
        registry_fields = [c for c in row.get("claims", []) if c.get("field") in
                           {"legal_name", "legal_form", "industry_code", "industry_description",
                            "registered_municipality", "registered_municipality_number",
                            "last_submitted_accounts_year"}]
        if registry_fields != base["claims"]:
            errors.append(f"{org}: V0 registry claims modified")
        base_evs = {ev["id"] for ev in base["evidence"]}
        if not base_evs.issubset({ev.get("id") for ev in row.get("evidence", [])}):
            errors.append(f"{org}: V0 evidence removed")
        official_claims = [c for c in row.get("claims", []) if c not in base["claims"]]
        evidence_map = {e.get("id"): e for e in row.get("evidence", []) if isinstance(e, dict)}
        if len(evidence_map) != len(row.get("evidence", [])):
            errors.append(f"{org}: duplicate evidence IDs")
        expected_map: dict[str, tuple[str, dict[str, Any], SourceResponse]] = {}
        for module in MODULES:
            status = row.get("availability", {}).get(module)
            response = source_responses.get((org, module))
            if response is None:
                if status not in {"not_applicable", "failed"}:
                    errors.append(f"{org}: unchecked source mislabeled as {status}")
                if status == "failed" and row.get("official_modules", {}).get(module, {}).get("reason") not in {"not_attempted", "budget_exhausted_not_checked", "identity_not_verified"}:
                    errors.append(f"{org}: missing explanation for unchecked module")
                continue
            if status not in {"available", "not_available", "blocked", "ambiguous", "failed"}:
                errors.append(f"{org}: invalid module availability {module}")
            if response.status != 200:
                if status != _failure_state(response):
                    errors.append(f"{org}: incorrect HTTP-to-state mapping {module}")
                continue
            if not verified_payload(response):
                if status != "failed":
                    errors.append(f"{org}: unverified source accepted {module}")
                continue
            try:
                candidates, state = extract(module, org, response.body,
                                            frozen_legal_name=base["legal_identity"].get("legal_name"))
                if any(not locator_proves_fact(module, org, response.body, fact) for fact in candidates):
                    if status != "failed":
                        errors.append(f"{org}: raw source does not prove facts {module}")
                    continue
                if status != state:
                    # Parser errors legitimately convert state to failed; no claims.
                    if status != "failed":
                        errors.append(f"{org}: normalized state mismatch {module}")
                    continue
                semantic = sha(response.body)
                if row["official_modules"][module].get("semantic_sha256") != semantic:
                    errors.append(f"{org}: semantic hash mismatch {module}")
                if row["official_modules"][module].get("content_sha256") != response.content_sha256:
                    errors.append(f"{org}: raw source digest mismatch {module}")
                page_metadata = ([{"url": p.url, "content_sha256": p.content_sha256,
                                   "semantic_sha256": sha(p.body), "retrieved_at": p.retrieved_at}
                                  for p in response.pages] if response.pages else None)
                if row["official_modules"][module].get("page_provenance") != page_metadata:
                    errors.append(f"{org}: official page provenance mismatch {module}")
                if row["official_modules"][module].get("source_complete") != source_is_complete(module, response.body):
                    errors.append(f"{org}: false source completeness {module}")
                for key in ("continuation_state", "continuation_http_status", "continuation_reason"):
                    if row["official_modules"][module].get(key) != getattr(response, key):
                        errors.append(f"{org}: incorrect continuation {key} {module}")
                if state == "available":
                    seen_values = set()
                    for fact in candidates:
                        identity = canonical_bytes([fact["field"], fact["value"], fact["reporting_period"],
                                                    fact["locator"] if module == "roles" else None])
                        if identity in seen_values:
                            continue
                        seen_values.add(identity)
                        evidence_id = _hash_claim(org, module, fact["field"], fact["value"], semantic, fact["locator"])
                        expected_map[evidence_id] = (module, fact, response)
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                if status != "failed":
                    errors.append(f"{org}: invalid source published {module}")
        seen_claim_ids = set()
        for claim in official_claims:
            if claim.get("organisation_number") != org or claim.get("availability") != "available" or claim.get("scope") != "exact_legal_entity":
                errors.append(f"{org}: invalid published claim scope/status")
                continue
            ids = claim.get("evidence_ids")
            if not isinstance(ids, list) or len(ids) != 1 or ids[0] not in expected_map:
                errors.append(f"{org}: unsupported official claim")
                continue
            eid = ids[0]
            if eid in seen_claim_ids:
                errors.append(f"{org}: duplicate canonical claim")
            seen_claim_ids.add(eid)
            module, fact, response = expected_map[eid]
            evidence = evidence_map.get(eid)
            source_url, source_digest, page_locator, observed_at = origin_observation_for_fact(response, fact["locator"])
            if (claim.get("field") != fact["field"] or claim.get("value") != fact["value"] or
                    claim.get("reporting_period") != fact["reporting_period"] or
                    not isinstance(evidence, dict) or evidence.get("source_url") != source_url or
                    evidence.get("source_row_key") != org or evidence.get("content_sha256") != source_digest or
                    (evidence.get("source_page_locator") if response.pages else None) !=
                    (page_locator if response.pages else None) or
                    evidence.get("semantic_sha256") != sha(response.body) or
                    evidence.get("locator") != fact["locator"] or
                    evidence.get("extraction_method") != "starter_normalizer_verified" or
                    evidence.get("claim_span") != canonical_bytes([fact["field"], fact["value"]]).decode("utf-8") or
                    evidence.get("retrieved_at") != observed_at):
                errors.append(f"{org}: official claim not backed by exact source")
        if seen_claim_ids != set(expected_map):
            errors.append(f"{org}: missing or surplus normalized claims")
        official_evidence_ids = {e.get("id") for e in row.get("evidence", [])
                                 if isinstance(e, dict) and e.get("source_class") == "official_brreg_api"}
        for evidence in row.get("evidence", []):
            if not isinstance(evidence, dict) or evidence.get("source_class") != "official_brreg_api":
                continue
            try:
                event_time = datetime.fromisoformat(evidence["retrieved_at"].replace("Z", "+00:00"))
                finished_time = datetime.fromisoformat(row["run"]["completed_at"].replace("Z", "+00:00"))
                if (event_time.utcoffset() is None or finished_time.utcoffset() is None or
                        event_time > finished_time):
                    errors.append(f"{org}: evidence timing invalid")
            except (KeyError, TypeError, AttributeError, ValueError):
                errors.append(f"{org}: invalid evidence timestamp")
        if official_evidence_ids != seen_claim_ids:
            errors.append(f"{org}: orphan or missing official evidence")
        for module in MODULES:
            response = source_responses.get((org, module))
            if response is None or response.status != 200:
                continue
            state = row["availability"].get(module)
            if state in {"available", "not_available", "ambiguous"}:
                matched = [s for s in row["source_snapshots"]
                           if isinstance(s, dict) and s.get("source_id") == module]
                if (len(matched) != 1 or matched[0].get("organisation_number") != org or
                    matched[0].get("source_url") != response.url or
                    matched[0].get("retrieved_at") != response.retrieved_at or
                    matched[0].get("snapshot_sha256") != response.content_sha256 or
                    matched[0].get("semantic_sha256") != sha(response.body)):
                    errors.append(f"{org}: missing/incorrect source snapshot for {module}")
    return {"passed": not errors, "errors": errors[:60], "error_count": len(errors),
            "official_v2_schema_verified": False}


def validate_v1(rows: list[dict[str, Any]], orgs: list[str], bases: list[dict[str, Any]],
                source_responses: dict[tuple[str, str], SourceResponse],
                previous_rows: list[dict[str, Any]] | None = None,
                previous_sha256: str | None = None) -> dict[str, Any]:
    """A malformed envelope is a validation FAIL, never an uncaught exception."""
    try:
        return _validate_v1_impl(rows, orgs, bases, source_responses,
                                 previous_rows, previous_sha256)
    except (TypeError, ValueError, KeyError, IndexError, AttributeError,
            OverflowError, RecursionError) as exc:
        return {"passed": False, "errors": [f"invalid envelope shape: {type(exc).__name__}"],
                "error_count": 1, "official_v2_schema_verified": False}


def _enforce_evidence_cutoff(response: SourceResponse, org: str,
                             cutoff: datetime | None) -> SourceResponse:
    """Withhold post-cutoff pages, retaining an authenticated contiguous prefix.

    No page timestamp is rewritten. If page 0 is ineligible, no page can be
    safely published as the start of a complete, consistently paginated set.
    All actually attempted requests remain counted in the returned response.
    """
    if cutoff is None or response.status != 200:
        return response
    def allowed(value: str) -> bool:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if observed.utcoffset() is None:
            raise ValueError("Source timestamp must be timezone aware")
        return observed <= cutoff
    if not response.pages:
        if allowed(response.retrieved_at):
            return response
        return SourceResponse(response.module, response.url, 0, response.retrieved_at,
                              error="evidence after evaluator cutoff", requests=response.requests)
    eligible = []
    for page in response.pages:
        if not allowed(page.retrieved_at):
            break
        eligible.append(page)
    if len(eligible) == len(response.pages):
        return response
    if not eligible:
        return SourceResponse(response.module, response.url, 0, response.retrieved_at,
                              error="first source page after evaluator cutoff", requests=response.requests)
    accounted = sum(page.requests for page in eligible)
    return assemble_location_pages(org, eligible, failed_attempts=response.requests-accounted,
                                   continuation_state="cutoff_limited",
                                   continuation_reason="later page outside evaluator cutoff")


def _bounded_fetch(fetch: Callable[[str, str], SourceResponse], module: str, org: str,
                   max_wait: float) -> SourceResponse:
    """Bound caller wall time; abandoned daemon transport is never used as evidence.

    Python cannot forcibly cancel a blocked synchronous system call. The runner
    stops scheduling subsequent requests if an abandoned worker is detected.
    """
    channel: queue.Queue[Any] = queue.Queue(maxsize=1)
    def invoke() -> None:
        try:
            channel.put((True, fetch(module, org)), block=False)
        except BaseException as exc:
            channel.put((False, exc), block=False)
    worker = threading.Thread(target=invoke, daemon=True, name="bounded-brreg-fetch")
    worker.start()
    try:
        ok, outcome = channel.get(timeout=max(0.001, max_wait))
    except queue.Empty:
        raise TimeoutError("Official fetch exceeded its remaining wall deadline")
    if not ok:
        raise outcome
    return outcome


def run(*, organisations: Path, bulk: Path, output: Path, report: Path,
        run_id: str, expected_count: int | None, expected_bulk_sha256: str | None,
        fixture_mode: bool = False, fixture_responses: Path | None = None,
        allow_live: bool = False, max_requests: int = 500, max_seconds: float = 180.0,
        checkpoint_every: int = 10,
        fetcher: Callable[[str, str], SourceResponse] | None = None,
        previous_output: Path | None = None,
        expected_previous_sha256: str | None = None,
        evidence_cutoff: str | None = None) -> dict[str, Any]:
    if not run_id or max_requests < 0 or max_seconds <= 0 or checkpoint_every < 1:
        raise ValueError("Invalid run configuration")
    if fetcher is not None and not fixture_mode:
        raise ValueError("Injected transport may only run in explicitly labeled synthetic mode")
    if evidence_cutoff is not None:
        try:
            cutoff_date = datetime.fromisoformat(evidence_cutoff.replace("Z", "+00:00"))
            if cutoff_date.tzinfo is None:
                raise ValueError("Cutoff requires timezone")
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid evidence cutoff timestamp") from exc
    else:
        cutoff_date = None
    if allow_live == bool(fixture_responses) and fetcher is None:
        raise ValueError("Select exactly one of --allow-live or --fixture-responses")
    if fixture_mode != (fixture_responses is not None) and fetcher is None:
        raise ValueError("Offline fixtures require --fixture-mode; live mode forbids fixture-mode")
    orgs = parse_batch(organisations)
    if not orgs:
        raise ValueError("Empty V1 batches are not permitted")
    if expected_count is not None and len(orgs) != expected_count:
        raise ValueError("Input count does not match expected count")
    protected = {organisations.resolve(), bulk.resolve()}
    if fixture_responses:
        protected.add(fixture_responses.resolve())
    if previous_output:
        protected.add(previous_output.resolve())
        if not expected_previous_sha256:
            raise ValueError("V1 refresh requires independent prior-output SHA-256")
    if output.resolve() in protected or report.resolve() in protected or output.resolve() == report.resolve():
        raise ValueError("Output paths overlap input artifacts")
    started = utc_now()
    start_clock = time.monotonic()
    rows = [placeholder(org, run_id, started) for org in orgs]
    atomic_jsonl(output, rows)  # Crash-safe complete envelopes before any parsing/fetch.
    status = "failed"
    errors = []
    validation = {"passed": False, "errors": ["not processed"]}
    baseline_report: dict[str, Any] = {}
    attempted = 0
    responses: dict[tuple[str, str], SourceResponse] = {}
    previous_rows: list[dict[str, Any]] | None = None
    partial_budget = False
    abandoned_transport = False
    try:
        if previous_output:
            # Loaded *after* a complete fail-safe artifact is on disk.
            previous_rows = load_prior(previous_output, expected_previous_sha256, orgs)
        if fixture_responses:
            fixture = json.loads(fixture_responses.read_text(encoding="utf-8"))
            if not isinstance(fixture, dict):
                raise ValueError("Fixture must be a JSON object")
            fetch = lambda module, org: fixture_fetch(module, org, fixture)
        else:
            # Constrain location page requests by the *remaining* global budget.
            # No shared mutable worker state is modified by the transport.
            fetch = fetcher if fetcher is not None else (
                lambda module, org: live_fetch(module, org,
                    max_requests=max(1, max_requests - attempted),
                    policy=NetworkPolicy.AUTHORIZED_PUBLIC_BRREG))
        bases, baseline_report = _v0_identity_baseline(orgs, bulk, run_id, started,
                                                        expected_bulk_sha256)
        rows = json.loads(json.dumps(bases))  # Immutable comparison copy retained.
        for row in rows:
            # A checkpoint written during research must never claim the whole
            # research run already completed. Only the final verified output
            # receives the completed terminal state.
            row["run"]["terminal_status"] = "failed"
            row["official_modules"] = {}
            row["operations"]["official_fetch_ms"] = 0
            row["operations"]["official_request_count"] = 0
            for module in MODULES:
                # An unattempted module is UNKNOWN, never a verified negative.
                row["availability"][module] = "failed"
                row["official_modules"][module] = {"state": "failed", "reason": "not_attempted"}
        atomic_jsonl(output, rows)
        # Budget-aware bounded breadth. A reduced budget costs coverage, NOT the
        # integrity of otherwise completed company envelopes. No source can
        # publish a fact unless actually fetched and independently checked.
        # Registry-live is redundant for the pinned identity baseline; prioritize
        # registered roles/locations and accounts first, then name freshness.
        selected = ("roles", "locations", "financials", "registry_live")
        # The V0 baseline is a required local identity gate, not an official
        # transport attempt.  Start the bounded external-source deadline only
        # after that gate has completed, otherwise a tiny transport budget can
        # expire before the first request and mask a blocked transport as mere
        # budget exhaustion.
        transport_started = time.monotonic()
        # Apply a stable rotation so a prefix in the official batch never gains
        # priority just because the input file lists it first.
        ordered = sorted(range(len(orgs)), key=lambda i: hashlib.sha256(orgs[i].encode()).digest())
        completed_fetch_attempts = 0
        for module in selected:
            for index in ordered:
                org = orgs[index]
                remaining = max_seconds - (time.monotonic() - transport_started)
                # Very short budgets still need enough deterministic time to
                # turn the timeout into complete terminal envelopes and write
                # the final atomic checkpoint.  A 10% reserve (4.5ms in the
                # adversarial 45ms case) is not schedulable on Windows.
                reserve = min(2.0, max(max_seconds * 0.10, 0.030))
                if attempted >= max_requests or remaining <= reserve or abandoned_transport:
                    partial_budget = True
                    rows[index]["availability"][module] = "failed"
                    rows[index]["official_modules"][module] = {
                        "state": "failed", "reason": "budget_exhausted_not_checked"}
                    continue
                if rows[index]["availability"].get("registry") != "available":
                    rows[index]["availability"][module] = "failed"
                    rows[index]["official_modules"][module] = {
                        "state": "failed", "reason": "identity_not_verified"}
                    continue
                call_started = time.monotonic()
                try:
                    timeout = min(4.0, max(0.001, remaining - reserve))
                    response = _bounded_fetch(fetch, module, org, timeout)
                    if (not isinstance(response, SourceResponse) or response.module != module or
                            response.url != endpoint(module, org)):
                        raise ValueError("Fetcher returned untrusted endpoint metadata")
                    if (type(response.requests) is not int or not 0 <= response.requests <=
                            max_requests - attempted or (module != "locations" and response.requests > 1)):
                        raise ValueError("Invalid source request accounting")
                    if not fixture_mode and response.status == 200 and response.requests < 1:
                        raise ValueError("Unaccounted successful live source")
                    response = _enforce_evidence_cutoff(response, org, cutoff_date)
                except TimeoutError:
                    abandoned_transport = True
                    response = SourceResponse(module, endpoint(module, org), 0, utc_now(),
                                              error="transport: hard deadline exceeded",
                                              requests=0 if fixture_mode else 1)
                except Exception as exc:
                    response = SourceResponse(module, endpoint(module, org), 0, utc_now(),
                                              error=f"transport: {type(exc).__name__}",
                                              requests=0 if fixture_mode else 1)
                attempted += response.requests
                responses[(org, module)] = response
                _enhance(rows[index], module, response)
                elapsed = max(0, int((time.monotonic() - call_started) * 1000))
                rows[index]["operations"]["official_fetch_ms"] += elapsed
                rows[index]["operations"]["official_request_count"] += response.requests
                rows[index]["operations"]["requests"] += response.requests
                completed_fetch_attempts += 1
                if completed_fetch_attempts % checkpoint_every == 0:
                    atomic_jsonl(output, rows)
            # Once a non-cooperative transport has consumed the hard deadline,
            # every remaining module is marked as not checked in memory.  Do
            # not spend the caller's remaining wall-time repeatedly rewriting
            # identical checkpoints; the final validated checkpoint below is
            # still atomic and contains every terminal envelope.
            if not abandoned_transport:
                atomic_jsonl(output, rows)
        completed_at = utc_now()
        for row in rows:
            row["run"]["completed_at"] = completed_at
            row["run"]["terminal_status"] = "completed"
            row["operations"]["runtime_ms"] = int((time.monotonic() - transport_started) * 1000)
        previous_map = prior_by_org(previous_rows) if previous_rows is not None else {}
        for row in rows:
            org = row["organisation_number"]
            row.update(refresh_view(row, previous_map.get(org),
                                    expected_previous_sha256 if org in previous_map else None))
        atomic_jsonl(output, rows)
        validation = validate_v1(rows, orgs, bases, responses, previous_rows,
                                 expected_previous_sha256 if previous_rows is not None else None)
        status = "completed" if not errors and validation["passed"] else "failed"
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {str(exc)[:220]}")
        # Fail closed: initial placeholders or last committed output remain intact.
        status = "failed"
    result = {
        "run_id": run_id, "run_status": status, "started_at": started,
        "completed_at": utc_now(), "input_count": len(orgs),
        "output_count": len(read_jsonl(output)), "source_requests": attempted,
        "source_mode": ("SYNTHETIC_INJECTED_TRANSPORT" if fetcher is not None else
                        "SYNTHETIC_FIXTURE" if fixture_mode else "LIVE_OFFICIAL_ENDPOINTS"),
        "partial_research_due_to_budget": partial_budget,
        "unrecoverable_transport_timeout": abandoned_transport,
        "evidence_cutoff": evidence_cutoff,
        "baseline_gate": baseline_report.get("validated_outputs", {}).get("passed", False),
        "validation": validation, "errors": errors,
        "wall_clock_ms": int((time.monotonic() - start_clock) * 1000),
        "official_v2_schema_verified": False, "official_score": None,
        "official_release_ready": False,
    }
    atomic_json(report, result)
    return result
