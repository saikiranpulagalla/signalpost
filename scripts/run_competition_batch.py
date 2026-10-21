#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
import os
import sys
import threading
import time
from urllib.parse import urlsplit
from contextlib import contextmanager, ExitStack
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.batch import profile_complete_for_modules, profiles_from_bulk, read_organisation_inputs, terminal_envelope, validate_envelopes  # noqa: E402
from norway_company_agent.evidence import evidence, utc_now  # noqa: E402
from norway_company_agent.http import FetchResult, fetch_json, transport_stop_reason  # noqa: E402
from norway_company_agent.network_policy import NetworkPolicy, ServiceCooldown  # noqa: E402
from norway_company_agent.telemetry import RequestTelemetry, utc_now as telemetry_utc_now  # noqa: E402
from norway_company_agent.telemetry import source_family, source_organisation_number  # noqa: E402
from norway_company_agent.source_snapshots import SourceSnapshotStore  # noqa: E402
from norway_company_agent.official import fetch_official_modules  # noqa: E402
from norway_company_agent.research_result import attach_research_result  # noqa: E402
from norway_company_agent.live_bootstrap import candidate_profiles, resolve_live_identity  # noqa: E402
from norway_company_agent.evaluator_adapter import adapt_terminal_envelope, validate_starter_envelopes  # noqa: E402
from norway_company_agent.profile_integrity import validate_metadata, verify_baseline, verify_live_baseline, verify_source_record, verify_claims, observation_key, receipt, verify_receipt, verify_history, sign_history  # noqa: E402
from norway_company_agent.admission import admit_wave, plan_family_admission, latency_forecasts, admitted_modules_by_org  # noqa: E402
from v0_kernel.storage import atomic_json, atomic_jsonl, read_jsonl, read_jsonl_bytes  # noqa: E402
from v0_kernel.json_codec import loads as strict_loads, dumps as strict_dumps  # noqa: E402


_owned_locks = threading.local()


@contextmanager
def _output_lock(path: Path):
    """Reject concurrent writers; OS releases a dead process's advisory lock."""
    identity = str(path.resolve())
    owned = getattr(_owned_locks, "paths", set())
    if identity in owned:
        yield
        return
    lock = path.with_suffix(path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    handle = lock.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError as exc:
                raise RuntimeError(f"Concurrent writer rejected for {path}") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError as exc:
                raise RuntimeError(f"Concurrent writer rejected for {path}") from exc
        # A Windows byte-range lock is mandatory for overlapping I/O.  Seed
        # the one-byte anchor only after locking it, otherwise a contender
        # can fail at read() before it reaches the non-blocking lock attempt.
        handle.seek(0)
        if not handle.read(1):
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
        _owned_locks.paths = owned | {identity}
        yield
    finally:
        _owned_locks.paths = owned
        if acquired:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with _output_lock(path):
        atomic_jsonl(path, rows)


def main() -> None:
    execution_started = time.monotonic()
    parser = argparse.ArgumentParser(description="Evaluator-owned Signalpost batch contract")
    parser.add_argument("--organisations", required=True, help="JSON, JSONL, or text organisation-number list")
    parser.add_argument("--bulk", help="Frozen Brreg entity snapshot (required for frozen_replay)")
    parser.add_argument("--live-bootstrap", action="store_true",
                        help="Resolve current identities from BRREG after the caller authorizes live sources")
    parser.add_argument("--confirm-authorized-live-sources", action="store_true",
                        help="Required with --live-bootstrap before any live BRREG request is started")
    parser.add_argument("--network-policy", choices=tuple(item.value for item in NetworkPolicy),
                        default=NetworkPolicy.OFFLINE.value,
                        help="Outbound source policy; offline is safe by default")
    parser.add_argument("--output", required=True, help="Terminal envelope JSONL")
    parser.add_argument("--profiles-output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--completion-manifest", help="Optional completed-run manifest; defaults beside report")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-count", type=int, help="Optional evaluator-supplied batch count; defaults to input size")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--max-source-requests", type=int,
                        help="Bound official endpoint calls; requires --source-attempts 1")
    parser.add_argument("--source-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--source-attempts", type=int, default=1)
    parser.add_argument("--max-runtime-seconds", type=float,
                        help="Global execution budget; source starts stop before the finalization reserve")
    parser.add_argument("--finalization-reserve-seconds", type=float, default=30.0,
                        help="Time retained for validation, atomic writes, report, and completion manifest")
    parser.add_argument("--source-priority", default="financials,roles,locations,group,registry_live",
                        help="Comma-separated optional-source order")
    parser.add_argument("--source-family-budgets", default="",
                        help="Optional comma-separated caps such as financials=1000,group=0")
    parser.add_argument("--request-telemetry", type=Path,
                        help="Optional new JSONL file for per-request metadata; no response bodies are recorded")
    parser.add_argument("--source-snapshots-dir", type=Path,
                        help="Optional new content-addressed directory for permitted public-source bytes")
    parser.add_argument("--source-snapshot-byte-cap", type=int, default=25_000_000)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verified-source-snapshots", type=Path, help="Read-only captured bytes used to authenticate prior optional observations")
    parser.add_argument("--observation-key", type=Path, help="Private local normalized-observation authentication key; defaults beside report; never distribute")
    parser.add_argument("--previous-profiles", type=Path, help="Explicit authenticated previous-run input for refresh; distinct from resume")
    parser.add_argument("--previous-bulk", type=Path, help="Authoritative frozen registry bytes for the previous observation")
    parser.add_argument("--source-latency-model", type=Path, help="JSON family -> conservative expected seconds; forecasts, not official limits")
    parser.add_argument("--source-allocation-policy", choices=("family_waves", "core_breadth"), default="family_waves",
                        help="Optional matched financial/role breadth reservation before residual family waves")
    parser.add_argument("--viewer-output", type=Path, help="Generate the verification viewer from this run's profiles")
    parser.add_argument("--evidence-cutoff-policy", type=Path, help="Reserved organizer-supplied policy; rejected until semantics are supported")
    parser.add_argument("--modules", default="registry,accounting_obligation")
    parser.add_argument("--optional-research-policy", choices=("strict", "best_effort"), default="strict",
                        help="Opt-in terminal baseline publication with explicit unattempted optional-work diagnostics; integrity failures remain fatal")
    parser.add_argument("--output-target", choices=("internal_v2", "starter_compatible"), default="internal_v2")
    args = parser.parse_args()
    args.execution_started = execution_started
    args.publication_started = False
    manifest = Path(args.completion_manifest) if args.completion_manifest else Path(args.report).with_suffix(Path(args.report).suffix + ".completion.json")
    paths = [Path(args.output), Path(args.profiles_output), Path(args.report), manifest]
    if args.viewer_output:
        paths.append(args.viewer_output)
    if args.request_telemetry:
        paths.append(args.request_telemetry)
    if len({str(path.resolve()) for path in paths}) != len(paths):
        raise SystemExit("Run artifact paths must be distinct")
    # Acquire in canonical order before altering ANY run artifact. Locks are
    # OS-owned, reentrant only within this thread, and released on process death.
    with ExitStack() as ownership:
        for path in sorted(paths, key=lambda item: str(item.resolve())):
            ownership.enter_context(_output_lock(path))
        try:
            _run(args)
        except Exception as exc:
            report = Path(args.report)
            if args.publication_started and report.exists():
                atomic_json(report, {"run_id": args.run_id, "status": "failed",
                                    "validation": {"passed": False, "errors": [type(exc).__name__ + ": " + str(exc)]}})
            raise


def _run(args) -> None:
    deadline_started = getattr(args, "execution_started", time.monotonic())
    if args.evidence_cutoff_policy:
        raise SystemExit("Organizer cutoff semantics are not implemented; refusing to guess")
    if bool(args.previous_profiles) != bool(args.previous_bulk):
        raise SystemExit("Refresh requires both --previous-profiles and --previous-bulk")
    if args.previous_profiles and args.resume:
        raise SystemExit("Refresh and same-run resume are separate operations")
    # Refuse before creating a report, manifest, or any transport object.  The
    # smoke wrapper also enforces this, but the underlying runner is callable.
    if args.live_bootstrap and not args.confirm_authorized_live_sources:
        raise SystemExit("Refusing live requests without --confirm-authorized-live-sources")
    if args.network_policy == NetworkPolicy.AUTHORIZED_PUBLIC_BRREG.value and not args.confirm_authorized_live_sources:
        raise SystemExit("Authorized BRREG policy requires --confirm-authorized-live-sources")
    requested_at_boundary = [item.strip() for item in args.modules.split(",") if item.strip()]
    live_modules_at_boundary = set(requested_at_boundary) - {"registry", "accounting_obligation", "website"}
    if args.live_bootstrap:
        live_modules_at_boundary.discard("registry_live")
    # This preliminary check exists solely to preserve the no-artifact
    # rejection boundary.  Full family-budget syntax validation occurs below;
    # only an exact ``family=0`` is sufficient to declare a source family
    # intentionally unscheduled before the runner creates any files.
    zero_allocations_at_boundary = {
        item.partition("=")[0].strip()
        for item in args.source_family_budgets.split(",")
        if item.strip().partition("=")[2].strip() == "0"
    }
    scheduled_live_modules_at_boundary = {
        module for module in live_modules_at_boundary
        if ("financials" if module == "financial_history" else module) not in zero_allocations_at_boundary
    }
    if args.live_bootstrap and args.network_policy != NetworkPolicy.AUTHORIZED_PUBLIC_BRREG.value:
        raise SystemExit("--live-bootstrap requires --network-policy authorized_public_brreg")
    if args.network_policy == NetworkPolicy.AUTHORIZED_PUBLIC_BRREG.value and (scheduled_live_modules_at_boundary or args.live_bootstrap) and args.source_attempts != 1:
        raise SystemExit("Authorized runner accounting requires one physical attempt per operation; retries are not independently instrumented")
    if (args.network_policy == NetworkPolicy.OFFLINE.value and scheduled_live_modules_at_boundary and
            args.max_source_requests != 0):
        raise SystemExit(
            "Offline policy forbids scheduled live enrichment modules; use registry,accounting_obligation, "
            "an explicit zero source-family allocation, a local fixture, or an explicitly authorized BRREG policy"
        )
    started_at = utc_now()
    report_path = Path(args.report)
    manifest_path = Path(args.completion_manifest) if args.completion_manifest else report_path.with_suffix(report_path.suffix + ".completion.json")
    organisation_inputs = read_organisation_inputs(args.organisations)
    orgs = [item["organisation_number"] for item in organisation_inputs]
    if args.expected_count is not None and len(orgs) != args.expected_count:
        raise SystemExit(f"Expected {args.expected_count} organisations, received {len(orgs)}")
    if args.workers < 1:
        raise SystemExit("--workers must be at least 1")
    if args.checkpoint_every < 1:
        raise SystemExit("--checkpoint-every must be at least 1")
    if args.max_source_requests is not None and args.max_source_requests < 0:
        raise SystemExit("--max-source-requests must be non-negative")
    if args.source_timeout_seconds <= 0:
        raise SystemExit("--source-timeout-seconds must be positive")
    if args.source_attempts < 1:
        raise SystemExit("--source-attempts must be at least 1")
    if args.max_runtime_seconds is not None and args.max_runtime_seconds <= args.finalization_reserve_seconds:
        raise SystemExit("--max-runtime-seconds must exceed --finalization-reserve-seconds")
    if args.finalization_reserve_seconds < 0:
        raise SystemExit("--finalization-reserve-seconds must be non-negative")
    if args.request_telemetry and args.request_telemetry.exists():
        raise SystemExit("--request-telemetry must name a new isolated file")
    if args.request_telemetry and args.source_attempts != 1:
        raise SystemExit("--request-telemetry requires --source-attempts 1 until each retry is independently instrumented")
    if args.source_snapshot_byte_cap < 0:
        raise SystemExit("--source-snapshot-byte-cap must be non-negative")
    if args.source_snapshots_dir and not args.live_bootstrap:
        raise SystemExit("--source-snapshots-dir is limited to explicitly authorised live-bootstrap runs")
    if args.source_snapshots_dir and args.source_snapshots_dir.exists():
        raise SystemExit("source snapshot directory must be new and isolated")
    if args.max_source_requests is not None and args.source_attempts != 1:
        raise SystemExit("--max-source-requests requires --source-attempts 1 for exact accounting")
    requested_modules = [item.strip() for item in args.modules.split(",") if item.strip()]
    if not requested_modules:
        raise SystemExit("--modules must name at least one module")
    unknown_modules = set(requested_modules) - {"registry", "accounting_obligation", "registry_live", "financials", "financial_history", "roles", "group", "locations", "website"}
    if unknown_modules:
        raise SystemExit(f"Unknown modules: {', '.join(sorted(unknown_modules))}")
    source_priority = [item.strip() for item in args.source_priority.split(",") if item.strip()]
    if len(source_priority) != len(set(source_priority)) or any(item not in unknown_modules | {"registry_live", "financials", "financial_history", "roles", "group", "locations"} for item in source_priority):
        raise SystemExit("--source-priority contains an invalid or duplicate source module")
    family_budgets: dict[str, int] = {}
    for item in (part.strip() for part in args.source_family_budgets.split(",") if part.strip()):
        family, separator, raw_limit = item.partition("=")
        if not separator or family not in {"registry_live", "financials", "roles", "locations", "group"}:
            raise SystemExit("--source-family-budgets must use known_family=nonnegative_integer")
        try:
            limit = int(raw_limit)
        except ValueError as exc:
            raise SystemExit("--source-family-budgets must use integers") from exc
        if limit < 0 or family in family_budgets:
            raise SystemExit("--source-family-budgets must be non-negative and unique")
        family_budgets[family] = limit
    # Refuse before opening a transport.  Explicit zero family allocations
    # are a pre-run plan omission and therefore do not need network access;
    # all remaining live modules do.
    if "registry" not in requested_modules or "accounting_obligation" not in requested_modules:
        raise SystemExit("registry and accounting_obligation are mandatory identity modules")
    if args.live_bootstrap and args.bulk:
        raise SystemExit("--live-bootstrap and --bulk are mutually exclusive identity modes")
    if not args.live_bootstrap and not args.bulk:
        raise SystemExit("--bulk is required unless --live-bootstrap is selected")
    if args.live_bootstrap and "registry_live" not in requested_modules:
        raise SystemExit("--live-bootstrap requires registry_live for exact current identity verification")
    if args.live_bootstrap:
        profiles, registry_metadata = candidate_profiles(orgs)
    else:
        profiles, registry_metadata = profiles_from_bulk(args.bulk, orgs)
        registry_metadata["identity_mode"] = "frozen_replay"
        if args.optional_research_policy == "best_effort" and registry_metadata.get("missing"):
            raise SystemExit("Best-effort optional research cannot recover missing official frozen identities")
    annotations = {item["organisation_number"]: item for item in organisation_inputs}
    for profile in profiles:
        for key in ("evaluation_split", "sample_slice"):
            if key in annotations[profile["organisation_number"]]:
                profile[key] = annotations[profile["organisation_number"]][key]
    fetch_modules = set(requested_modules) - {"registry", "accounting_obligation", "website"}
    if args.live_bootstrap:
        fetch_modules.discard("registry_live")
    # A zero family allocation is an intentional pre-run omission, not a
    # failed request and not evidence that a company has no such data.  Keep
    # it out of profile evidence entirely so the starter adapter does not
    # have to invent an availability value for work that was never selected.
    # Any non-zero allocation remains a scheduled operation: if it cannot be
    # reached later because of a global deadline/budget, ``_classified``
    # records ``not_fetched`` and starter publication fails closed.
    module_family = {
        "registry_live": "registry_live", "financials": "financials",
        "financial_history": "financials", "roles": "roles",
        "locations": "locations", "group": "group",
    }
    intentionally_omitted_modules = {
        module for module in fetch_modules
        if family_budgets.get(module_family[module]) == 0
    }
    scheduled_fetch_modules = fetch_modules - intentionally_omitted_modules
    planned_modules = set(requested_modules) - intentionally_omitted_modules
    # Admission is decided before any optional request starts.  A stable hash
    # distributes limited work over the batch instead of always enriching the
    # first input records.  ``registry_live`` is a mandatory identity phase
    # in live mode, so its worst-case one request per company is reserved.
    optional_order = [module for module in source_priority if module in scheduled_fetch_modules]
    optional_order.extend(sorted(scheduled_fetch_modules - set(optional_order)))
    admission_by_module: dict[str, list[str]] = {}
    omitted_by_module: dict[str, int] = {}
    remaining_global = None if args.max_source_requests is None else max(
        0, args.max_source_requests - (len(orgs) if args.live_bootstrap else 0)
    )
    expected_seconds = {family: args.source_timeout_seconds * args.source_attempts for family in module_family.values()}
    if args.source_latency_model:
        supplied = json.loads(args.source_latency_model.read_text(encoding="utf-8"))
        expected_seconds.update(latency_forecasts(supplied, args.source_timeout_seconds * args.source_attempts))
    remaining_seconds = None if args.max_runtime_seconds is None else max(0.0, args.max_runtime_seconds - args.finalization_reserve_seconds - (time.monotonic() - deadline_started))
    if args.live_bootstrap and remaining_seconds is not None:
        remaining_seconds = max(0.0, remaining_seconds - ((len(orgs) + args.workers - 1) // args.workers) * expected_seconds["registry_live"])
    ranked_candidates = {module: sorted(orgs, key=lambda org: hashlib.sha256(
        f"{args.run_id}|{module}|{org}".encode("utf-8")).hexdigest()) for module in optional_order}
    admission_by_module, omitted_by_module = plan_family_admission(ranked_candidates,
        module_family=module_family, expected_seconds=expected_seconds, remaining_seconds=remaining_seconds,
        request_slots=remaining_global,
        family_slots=family_budgets, workers=args.workers, policy=args.source_allocation_policy)
    admitted_by_org = admitted_modules_by_org(orgs, admission_by_module)
    execution_plan = {
        "required_baseline_modules": ["registry", "accounting_obligation"],
        "requested_optional_modules": sorted(fetch_modules),
        "scheduled_optional_modules": sorted(scheduled_fetch_modules),
        "not_scheduled_optional_modules": sorted(intentionally_omitted_modules),
        "admission": {
            "strategy": "sha256(run_id,module,organisation_number)",
            "admitted_organisations": admission_by_module,
            "intentionally_omitted_counts": omitted_by_module,
            "mandatory_identity_request_reserve": len(orgs) if args.live_bootstrap else 0,
            "expected_seconds_by_family": expected_seconds,
            "allocation_policy": args.source_allocation_policy,
        },
        "policy": ("best_effort optional research; admitted but unattempted work remains not_fetched in profiles and explicit terminal errors; verified baseline and publication integrity are mandatory"
                   if args.optional_research_policy == "best_effort" else
                   "zero source-family budget is an intentional pre-run omission; nonzero selected work must finish or fail closed"),
    }
    execution_plan_sha256 = hashlib.sha256(
        json.dumps(execution_plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    # Argument/input/path refusals above are non-mutating. Only now supersede
    # a previous completion; an actual failed execution must not retain success.
    args.publication_started = True
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"run_id": args.run_id, "status": "in_progress", "started_at": started_at}) + "\n", encoding="utf-8")
    atomic_json(manifest_path, {"run_id": args.run_id, "status": "in_progress", "started_at": started_at})
    operations = {"requests": 0, "bytes": 0, "latencies_ms": []}
    source_request_lock = threading.Lock()
    service_cooldown = ServiceCooldown()
    source_requests_started = 0
    source_family_started: dict[str, int] = {}
    transport_by_org: dict[str, dict] = {}
    telemetry = RequestTelemetry(run_id=args.run_id, output=args.request_telemetry)
    snapshots = SourceSnapshotStore(args.source_snapshots_dir, max_bytes=args.source_snapshot_byte_cap)
    private_key = observation_key(args.observation_key or report_path.with_suffix(report_path.suffix + ".observation-auth.key"))

    def bounded_fetch(url: str) -> FetchResult:
        """Account for an outbound endpoint call before starting it.

        An exact cap is only accepted with one transport attempt per endpoint;
        otherwise urllib retry attempts could exceed the declared budget.
        """
        nonlocal source_requests_started
        if args.live_bootstrap:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.hostname != "data.brreg.no" or parsed.username or parsed.password:
                now = telemetry_utc_now()
                telemetry.record(url=url, physical_request_number=None, started_at=now, completed_at=now,
                                 duration_ms=0, status=0, bytes_received=0,
                                 error="live source URL rejected by BRREG public-API policy")
                return FetchResult(url, 0, 0, 0, error="live source URL rejected by BRREG public-API policy", retrieved_at=now)
        physical_request_number = None
        ledger = None
        telemetry_started = telemetry_utc_now()

        def start_request() -> str | None:
            # Called immediately before the real opener, not before transport
            # policy checks. Never perform disk I/O while holding these locks.
            nonlocal source_requests_started, physical_request_number, ledger, telemetry_started
            with source_request_lock:
                family = source_family(url)
                stop_reason = "source service cooldown" if service_cooldown.blocked(url) else transport_stop_reason(url)
                if stop_reason:
                    return stop_reason
                if args.max_runtime_seconds is not None and (time.monotonic() - deadline_started) + expected_seconds.get(family, args.source_timeout_seconds) > (args.max_runtime_seconds - args.finalization_reserve_seconds):
                    return "execution deadline reserve reached"
                if args.max_source_requests is not None and source_requests_started >= args.max_source_requests:
                    return "source request budget exhausted"
                if family in family_budgets and source_family_started.get(family, 0) >= family_budgets[family]:
                    return "source family budget exhausted"
                source_requests_started += 1
                source_family_started[family] = source_family_started.get(family, 0) + 1
                physical_request_number = source_requests_started
                ledger = transport_by_org.setdefault(source_organisation_number(url), {"requests": 0, "bytes": 0, "latencies_ms": []})
                ledger["requests"] += 1
                telemetry_started = telemetry_utc_now()
            return None

        transport_started = time.monotonic()
        fetch_exception = None
        try:
            options = dict(timeout=args.source_timeout_seconds, attempts=args.source_attempts,
                           allow_redirects=False, policy=args.network_policy)
            parameters = inspect.signature(fetch_json).parameters
            if "deadline" in parameters:
                options["deadline"] = (deadline_started + args.max_runtime_seconds - args.finalization_reserve_seconds
                                       if args.max_runtime_seconds is not None else None)
            if "on_rate_limit" in parameters:
                options["on_rate_limit"] = lambda value: service_cooldown.limited(url, value)
            if "on_request_start" in parameters:
                options["on_request_start"] = start_request
                refusal = None
            else:
                # Deterministic fixture transport seam; a fixture call is its
                # simulated physical start and still uses the same atomic gate.
                refusal = start_request()
            result = (FetchResult(url, 0, 0, 0, error=refusal, retrieved_at=telemetry_utc_now(), request_started=False)
                      if refusal else fetch_json(url, **options))
        except Exception as exc:
            fetch_exception = exc
            result = FetchResult(url, 0, round((time.monotonic() - transport_started) * 1000), 0,
                                 error=type(exc).__name__, retrieved_at=telemetry_utc_now())
        if result.status == 429:
            service_cooldown.limited(url, result.retry_after)
        telemetry.record(url=url, physical_request_number=physical_request_number, started_at=telemetry_started,
                         completed_at=telemetry_utc_now(), duration_ms=result.elapsed_ms, status=result.status,
                         bytes_received=result.bytes_received, error=result.error,
                         retry_count=0, retry_after=result.retry_after)
        with source_request_lock:
            if ledger is not None:
                ledger["bytes"] += result.bytes_received
                ledger["latencies_ms"].append(result.elapsed_ms)
        # Only errors at the isolated transport call, after an accounted start,
        # may be ordinary source failures. Never swallow parser/validation errors
        # or failures before request admission. No source result/claim survives
        # this path; the attempted operation receives a truthful failed outcome.
        if (fetch_exception is not None and args.optional_research_policy == "best_effort"
                and physical_request_number is not None and not args.live_bootstrap
                and isinstance(fetch_exception, (OSError, RuntimeError))
                and not isinstance(fetch_exception, (ValueError, TypeError))):
            fetch_exception = None
        if fetch_exception is not None:
            raise fetch_exception
        if result.error == "StrictJSONError":
            raise ValueError("Non-finite source JSON; not a recoverable transport outcome")
        # Fresh successful transports must agree with their original bytes
        # BEFORE normalization or signing a retained observation. Completion
        # policy controls optional source failures, never evidence integrity.
        # Persisted legacy observations use the separate receipt/pin/source
        # verification path; no missing historical bytes are invented here.
        if result.status == 200 and not result.error:
            if (not result.body_complete or result.raw_bytes is None or
                    hashlib.sha256(result.raw_bytes).hexdigest() != result.content_sha256 or
                    len(result.raw_bytes) != result.bytes_received or strict_loads(result.raw_bytes) != result.body):
                raise ValueError("Source byte/projection integrity failure; not a recoverable transport outcome")
            strict_dumps(result.body)  # Also reject injected non-finite projections.
        if args.live_bootstrap:
            snapshots.retain(url=url, content_sha256=result.content_sha256, raw_bytes=result.raw_bytes,
                             metadata={"run_id": args.run_id, "http_status": result.status,
                                       "retrieved_at": result.retrieved_at})
        return result

    def enrich(profile: dict, modules: set[str], *, resolve_identity: bool = False) -> tuple[dict, dict]:
        not_started = {"source request budget exhausted", "source family budget exhausted", "execution deadline reserve reached", "network policy offline", "network policy rejected URL", "live source URL rejected by BRREG public-API policy", "source service cooldown", "transport service stopped"}
        identity_metrics: list[FetchResult] = []
        if resolve_identity and args.live_bootstrap:
            profile, identity_result, identity_verified = resolve_live_identity(profile, bounded_fetch)
            identity_metrics.append(identity_result)
            if identity_verified:
                for field in ("registry", "registry_live", "accounting_obligation"):
                    profile.setdefault("observation_receipts", {})[field] = receipt(profile, field, private_key)
            if not identity_verified:
                for module in modules:
                    profile["evidence"][module] = evidence(
                        module, "blocked", "official_source", "https://data.brreg.no/",
                        note="Current live identity verification unavailable; enrichment withheld",
                        source_row_key=profile["organisation_number"])
                if "website" in requested_modules:
                    profile["evidence"]["website"] = evidence(
                        "website", "blocked", "legacy_website_crawler_retired", "https://data.brreg.no/",
                        note="Live identity verification unavailable; website acquisition withheld")
                profile["run_metrics"] = {"requests": sum(item.error not in not_started for item in identity_metrics),
                                          "bytes": sum(item.bytes_received for item in identity_metrics),
                                          "latencies_ms": [item.elapsed_ms for item in identity_metrics]}
                return profile, profile["run_metrics"]
        if profile["evidence"]["registry"].get("status") != "available":
            for module in modules:
                profile["evidence"][module] = {
                    "field": module, "status": "blocked", "source_type": "official_source",
                    "source_class": "official_source", "source_url": "https://data.brreg.no/",
                    "retrieved_at": utc_now(), "note": "Frozen registry identity unavailable; live enrichment withheld",
                }
            if "website" in requested_modules:
                profile["evidence"]["website"] = evidence(
                    "website", "blocked", "legacy_website_crawler_retired", "https://data.brreg.no/",
                    note="Registry identity unavailable; website acquisition withheld")
            profile["run_metrics"] = {"requests": 0, "bytes": 0, "latencies_ms": []}
            return profile, profile["run_metrics"]
        ordered_modules = [module for module in source_priority if module in modules]
        ordered_modules.extend(sorted(modules - set(ordered_modules)))
        # Existing controlled worker-integration harnesses replace this
        # boundary with the historical ``(org, modules, fetcher)`` callable.
        # Retain that narrow seam while the production implementation accepts
        # an explicit ordering parameter.
        if "module_order" in inspect.signature(fetch_official_modules).parameters:
            records, metrics = fetch_official_modules(profile["organisation_number"], modules,
                                                      fetcher=bounded_fetch, module_order=ordered_modules)
        else:
            records, metrics = fetch_official_modules(profile["organisation_number"], modules,
                                                      fetcher=bounded_fetch)
        if not isinstance(records, dict) or set(records) - set(modules) or any(not isinstance(value, dict) for value in records.values()):
            raise ValueError("Malformed or unexpected worker evidence keys")
        if args.optional_research_policy == "best_effort":
            for record in records.values():
                value = record.get("value") or {}
                rejections = value.get("rejected_records", []) if isinstance(value, dict) else []
                reasons = [r.get("reason") if isinstance(r, dict) else r for r in rejections]
                if ("conflicts with requested identity" in str(record.get("note")) or
                        any(reason in {"organisation_number_mismatch", "missing_or_invalid_organisation_number", "parent_identity_mismatch", "invalid_or_missing_identity"} for reason in reasons)):
                    raise ValueError("Source identity integrity failure; not recoverable optional research")
        profile["evidence"].update(records)
        for field in records:
            if records[field].get("status") != "not_fetched":
                try:
                    signed = receipt(profile, field, private_key)
                except TypeError:
                    # Do not turn a serialization defect into an ordinary
                    # isolated source failure. Retain it for the atomic
                    # checkpoint/final writer to reject the whole run.
                    continue
                profile.setdefault("observation_receipts", {})[field] = signed
        website_metrics = {"requests": 0, "bytes": 0, "latencies_ms": []}
        if resolve_identity and "website" in requested_modules:
            # Keep optional crawler dependencies out of official-only runs.
            from norway_company_agent.identity import apply_website_identity_gate
            from norway_company_agent.website import fetch_website
            website_record, website_metrics = fetch_website(profile.get("website"))
            profile["evidence"]["website"] = apply_website_identity_gate(profile, website_record)["website"]
        metric = {
            "requests": sum(item.error not in not_started for item in identity_metrics + metrics) + website_metrics["requests"],
            "bytes": sum(item.bytes_received for item in identity_metrics + metrics) + website_metrics["bytes"],
            "latencies_ms": [item.elapsed_ms for item in identity_metrics + metrics] + website_metrics["latencies_ms"],
        }
        previous_metric = profile.get("run_metrics", {})
        profile["run_metrics"] = {
            "requests": previous_metric.get("requests", 0) + metric["requests"],
            "bytes": previous_metric.get("bytes", 0) + metric["bytes"],
            "latencies_ms": previous_metric.get("latencies_ms", []) + metric["latencies_ms"],
        }
        return profile, metric

    state: dict[str, dict] = {}
    worker_failures: list[dict[str, str]] = []
    resumed_profiles = 0
    profiles_output = Path(args.profiles_output)
    if args.resume and profiles_output.exists():
        prior = read_jsonl(profiles_output)
        if not set(item["organisation_number"] for item in prior).issubset(set(orgs)):
            raise SystemExit("Resume profile membership is not a subset of this batch")
        current_registry_hashes = {item["organisation_number"]: item["evidence"]["registry"].get("content_sha256") for item in profiles}
        current_baselines = {item["organisation_number"]: item for item in profiles}
        module_fingerprint = hashlib.sha256(",".join(requested_modules).encode("utf-8")).hexdigest()
        state = {}
        for item in prior:
            org = item.get("organisation_number")
            metadata = item.get("resume_contract")
            admitted_modules = metadata.get("admitted_optional_modules", []) if isinstance(metadata, dict) else []
            expected_for_profile = {"registry", "accounting_obligation", *admitted_modules}
            if (isinstance(org, str) and
                isinstance(metadata, dict) and metadata.get("identity_mode") == registry_metadata["identity_mode"] and
                metadata.get("modules_sha256") == module_fingerprint and
                metadata.get("execution_plan_sha256") == execution_plan_sha256 and
                (metadata.get("registry_snapshot_sha256") == current_registry_hashes.get(org) or
                 (args.live_bootstrap and metadata.get("baseline_input_sha256") == registry_metadata["registry_snapshot_sha256"]))):
                validate_metadata(item)
                if args.live_bootstrap:
                    verify_live_baseline(item, private_key)
                else:
                    verify_baseline(item, current_baselines[org])
                verify_claims(item)
                verify_history(item, private_key)
                if args.live_bootstrap:
                    from norway_company_agent.live_bootstrap import live_registry_projection
                    from norway_company_agent.official import normalize_entity
                    item.update(live_registry_projection(normalize_entity(item["evidence"]["registry"]["value"])))
                authenticated = []
                for module in admitted_modules:
                    if module in item.get("evidence", {}) and (verify_receipt(item, module, private_key) or verify_source_record(item, module, args.verified_source_snapshots)):
                        authenticated.append(module)
                    else:
                        item["evidence"].pop(module, None)
                item["authenticated_completed_modules"] = authenticated
                item["run_metrics"] = {"requests": 0, "bytes": 0, "latencies_ms": []}
                state[org] = item
        resumed_profiles = len(state)
    pending_profiles = [profile for profile in profiles if profile["organisation_number"] not in state]

    def checkpoint() -> None:
        write_jsonl(profiles_output, [state[org] for org in orgs if org in state])

    def save_profile(profile: dict, metric: dict) -> None:
        profile["resume_contract"] = {
            "identity_mode": registry_metadata["identity_mode"],
            "modules_sha256": hashlib.sha256(",".join(requested_modules).encode("utf-8")).hexdigest(),
            "execution_plan_sha256": execution_plan_sha256,
            "baseline_input_sha256": registry_metadata["registry_snapshot_sha256"],
            "admitted_optional_modules": admitted_by_org[profile["organisation_number"]],
            "registry_snapshot_sha256": profile["evidence"]["registry"].get("content_sha256"),
        }
        state[profile["organisation_number"]] = profile
        operations["requests"] += metric["requests"]
        operations["bytes"] += metric["bytes"]
        operations["latencies_ms"].extend(metric["latencies_ms"])

    def run_wave(label: str, candidates: list[dict], modules_for: callable, *, identity: bool = False) -> None:
        """Run one deterministic source-family wave across the batch."""
        if not candidates:
            return
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(enrich, copy.deepcopy(profile) if args.optional_research_policy == "best_effort" else profile,
                            modules_for(profile), resolve_identity=identity): profile
                for profile in candidates
            }
            for index, future in enumerate(as_completed(futures), 1):
                profile = futures[future]
                modules = modules_for(profile)
                try:
                    completed, metric = future.result()
                except Exception as exc:
                    completed = profile
                    for module in modules:
                        completed["evidence"][module] = evidence(
                            module, "source_error", "official_source", "https://data.brreg.no/",
                            note=f"Worker failure in {label}: {type(exc).__name__}")
                    metric = {"requests": 0, "bytes": 0, "latencies_ms": []}
                    worker_failures.append({"organisation_number": profile["organisation_number"], "error": type(exc).__name__})
                save_profile(completed, metric)
                if index % args.checkpoint_every == 0 or index == len(candidates):
                    checkpoint()

    # Identity is a separate wave. Frozen profiles already hold their trusted
    # baseline; live profiles must pass exact identity verification before
    # downstream waves can see them.
    if args.live_bootstrap:
        run_wave("live_identity", pending_profiles, lambda _: set(), identity=True)
    else:
        for profile in pending_profiles:
            save_profile(profile, {"requests": 0, "bytes": 0, "latencies_ms": []})
        if pending_profiles:
            checkpoint()

    if args.optional_research_policy == "best_effort" and any(
            profile["evidence"].get("registry", {}).get("status") != "available" for profile in state.values()):
        raise ValueError("Best-effort optional research requires every authoritative legal identity")

    for org, profile in state.items():
        if profile["evidence"].get("registry", {}).get("status") == "available":
            continue
        for module in admitted_by_org[org]:
            profile["evidence"].setdefault(module, evidence(
                module, "blocked", "official_source", "https://data.brreg.no/",
                note="Exact legal identity unavailable; admitted enrichment withheld",
                source_row_key=org))

    # Optional work is batch-wide by family, not company-by-company.  Only
    # admitted pairs reach this loop. Unselected pairs are deliberately absent
    # from evidence and remain visible in the immutable execution plan.
    for module in optional_order:
        admitted = set(admission_by_module[module])
        candidates = [state[org] for org in orgs if org in admitted and org in state and
                      state[org]["evidence"].get("registry", {}).get("status") == "available" and
                      module not in state[org].get("authenticated_completed_modules", [])]
        run_wave(module, candidates, lambda _profile, value=module: {value})

    completed_at = utc_now()
    if args.live_bootstrap:
        # Candidate selection is not identity verification.  Report the
        # result of the exact live checks only after all workers have settled.
        registry_metadata["selected"] = sum(
            item["evidence"].get("registry", {}).get("status") == "available" for item in state.values()
        )
        registry_metadata["missing"] = sum(
            item["evidence"].get("registry", {}).get("status") == "not_found" for item in state.values()
        )
        registry_metadata["failed"] = len(orgs) - registry_metadata["selected"] - registry_metadata["missing"]
    # This internal result layer is intentionally independent of the unknown
    # Builderr schema.  It makes every source-backed fact and citation usable
    # by local viewers while the terminal-envelope adapter remains isolated.
    for org, profile in state.items():
        if org in transport_by_org:
            profile["run_metrics"] = transport_by_org[org]
    operations = {"requests": sum(p.get("run_metrics", {}).get("requests", 0) for p in state.values()),
                  "bytes": sum(p.get("run_metrics", {}).get("bytes", 0) for p in state.values()),
                  "latencies_ms": [value for p in state.values() for value in p.get("run_metrics", {}).get("latencies_ms", [])]}
    ordered_profiles = [attach_research_result(state[org]) for org in orgs]
    if args.optional_research_policy == "best_effort":
        # All executor contexts have joined; validate detached observations before
        # any graceful-publication exception. No in-flight profile mutator remains.
        current_by_org = {p["organisation_number"]: p for p in profiles}
        for profile in ordered_profiles:
            validate_metadata(profile)
            if args.live_bootstrap:
                verify_live_baseline(profile, private_key)
            else:
                verify_baseline(profile, current_by_org[profile["organisation_number"]])
            verify_claims(profile)
    if args.previous_profiles:
        from norway_company_agent.refresh import TRACKED_FIELDS, supported_change
        previous_rows = read_jsonl(args.previous_profiles)
        previous_baselines, _ = profiles_from_bulk(args.previous_bulk, [p["organisation_number"] for p in previous_rows])
        prior_by_org = {p["organisation_number"]: p for p in previous_rows}
        baseline_by_org = {p["organisation_number"]: p for p in previous_baselines}
        if len(prior_by_org) != len(previous_rows):
            raise ValueError("Duplicate previous refresh identity")
        for previous in previous_rows:
            validate_metadata(previous)
            verify_baseline(previous, baseline_by_org[previous["organisation_number"]])
            verify_claims(previous)
            verify_history(previous, private_key)
        for profile in ordered_profiles:
            previous = prior_by_org.get(profile["organisation_number"])
            profile["changes"] = []
            if previous is None:
                continue
            history = {}
            historical = list(previous.get("historical_observations", []))
            authenticated = {"registry", "accounting_obligation"}
            for module, record in previous.get("evidence", {}).items():
                if record.get("status") == "available" and (module in authenticated or
                        verify_receipt(previous, module, private_key) or
                        verify_source_record(previous, module, args.verified_source_snapshots)):
                    authenticated.add(module)
                    history[module] = record
                    historical.append({"organisation_number": profile["organisation_number"],
                                       "module": module, "evidence": record})
            for field in TRACKED_FIELDS:
                module = field.split(".", 1)[0]
                old_record = previous.get("evidence", {}).get(module, {})
                new_record = profile.get("evidence", {}).get(module, {})
                if module not in authenticated:
                    continue
                change = supported_change(profile["organisation_number"], field, old_record, new_record)
                if change is not None:
                    profile["changes"].append(change)
            profile["previous_evidence"] = history
            profile["historical_observations"] = list({
                (row["module"], row["evidence"].get("content_sha256"), row["evidence"].get("retrieved_at")): row
                for row in historical}.values())
            sign_history(profile, private_key)
            verify_history(profile, private_key)
    # Refresh is computed after claim normalization; include only its authenticated events.
    ordered_profiles = [attach_research_result(profile) for profile in ordered_profiles]
    envelopes = [
        terminal_envelope(profile, run_id=args.run_id,
                          modules=sorted({"registry", "accounting_obligation", *admitted_by_org[profile["organisation_number"]]}),
                          started_at=started_at, completed_at=completed_at)
        for profile in ordered_profiles
    ]
    validation = validate_envelopes(envelopes, orgs)
    if args.optional_research_policy == "best_effort" and worker_failures:
        validation = {**validation, "passed": False,
                      "fatal_worker_errors": "Normalization/integrity worker failures are not graceful source outcomes"}
    publication_envelopes = envelopes
    output_validation = validation
    if args.output_target == "starter_compatible" and validation["passed"]:
        try:
            publication_envelopes = [adapt_terminal_envelope(item, observation_key=private_key,
                unattempted_optional_sources=(set(admitted_by_org[item["organisation_number"]])
                    if args.optional_research_policy == "best_effort" else frozenset())) for item in envelopes]
            output_validation = validate_starter_envelopes(publication_envelopes, orgs)
        except ValueError as exc:
            # The starter contract does not define a semantic equivalent for
            # this internal state.  Persist a diagnostic report but never
            # write a speculative terminal publication.
            output_validation = {"passed": False, "errors": [str(exc)]}
        validation = {**validation, "starter_compatible": output_validation,
                      "passed": validation["passed"] and output_validation["passed"]}
    write_jsonl(profiles_output, ordered_profiles)
    latencies = sorted(operations.pop("latencies_ms"))
    operations["p50_ms"] = latencies[len(latencies) // 2] if latencies else None
    operations["p95_ms"] = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None
    report = {
        "run_id": args.run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "expected_count": len(orgs),
        "emitted_envelopes": len(envelopes),
        "resumed_profiles": resumed_profiles,
        "profiles_fetched_this_run": len(pending_profiles),
        "modules": requested_modules,
        "identity_mode": registry_metadata["identity_mode"],
        "output_target": args.output_target,
        "registry": registry_metadata,
        "operations": operations,
        "source_request_policy": {
            "network_policy": args.network_policy,
            "max_source_requests": args.max_source_requests,
            "source_requests_started": source_requests_started,
            "source_timeout_seconds": args.source_timeout_seconds,
            "source_attempts": args.source_attempts,
            "max_runtime_seconds": args.max_runtime_seconds,
            "finalization_reserve_seconds": args.finalization_reserve_seconds,
            "source_priority": source_priority,
            "source_family_budgets": family_budgets,
            "source_family_started": source_family_started,
        },
        "execution_plan": {**execution_plan, "sha256": execution_plan_sha256},
        "request_telemetry": str(args.request_telemetry) if args.request_telemetry else None,
        "source_snapshots": {"directory": str(args.source_snapshots_dir) if args.source_snapshots_dir else None,
                             "byte_cap": args.source_snapshot_byte_cap, **snapshots.metrics},
        "worker_failures": worker_failures,
        **({"optional_research": {
            "policy": "best_effort",
            "internal_source_state_counts": {status: sum(
                p.get("evidence", {}).get(module, {}).get("status") == status
                for p in ordered_profiles for module in admitted_by_org[p["organisation_number"]])
                for status in ("available", "not_found", "blocked", "source_error", "not_fetched")},
            "note": "All profile workers joined; not_fetched stays selected/unattempted, not absence or an official availability state",
        }} if args.optional_research_policy == "best_effort" else {}),
        "validation": validation,
    }
    # A semantically invalid evaluator envelope must never cross the terminal
    # publication boundary.  Persist the diagnostic report, but retain the
    # last committed output rather than replacing it with invalid JSONL.
    if validation["passed"]:
        if args.max_runtime_seconds is not None and time.monotonic() - deadline_started >= args.max_runtime_seconds:
            raise TimeoutError("Overall execution budget expired before final publication")
        write_jsonl(Path(args.output), publication_envelopes)
        if args.viewer_output:
            from scripts.build_profile_viewer import render
            args.viewer_output.parent.mkdir(parents=True, exist_ok=True)
            args.viewer_output.write_text(render(ordered_profiles, observation_key=private_key), encoding="utf-8")
    report_path.write_text(strict_dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if validation["passed"] and not worker_failures:
        envelope_bytes = Path(args.output).read_bytes()
        committed = read_jsonl_bytes(envelope_bytes)
        if any((item.get("run", {}).get("run_id") if args.output_target == "starter_compatible" else item.get("run_id")) != args.run_id for item in committed):
            raise RuntimeError("Committed envelope run identity mismatch")
        if json.loads(report_path.read_text(encoding="utf-8"))["run_id"] != args.run_id:
            raise RuntimeError("Committed report run identity mismatch")
        completed_manifest = {
            "status": "completed", "run_id": args.run_id,
            "expected_input_sha256": hashlib.sha256("\n".join(orgs).encode("utf-8")).hexdigest(),
            "modules_sha256": hashlib.sha256(",".join(requested_modules).encode("utf-8")).hexdigest(),
            "execution_plan_sha256": execution_plan_sha256,
            "registry_snapshot_sha256": registry_metadata["registry_snapshot_sha256"],
            "identity_mode": registry_metadata["identity_mode"], "output_target": args.output_target,
            "envelope_sha256": hashlib.sha256(envelope_bytes).hexdigest(),
            "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "profiles_sha256": hashlib.sha256(profiles_output.read_bytes()).hexdigest(),
            "envelope_count": len(envelopes), "report_run_id": report["run_id"],
            "completed_at": completed_at,
        }
        from norway_company_agent.completion import verify_completion
        verify_completion(report=report_path, output=Path(args.output), profiles=profiles_output, candidate=completed_manifest)
        if args.max_runtime_seconds is not None and time.monotonic() - deadline_started >= args.max_runtime_seconds:
            raise TimeoutError("Overall execution budget expired before completion commit")
        atomic_json(manifest_path, completed_manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if validation["passed"] and not worker_failures else 1)


if __name__ == "__main__":
    main()
