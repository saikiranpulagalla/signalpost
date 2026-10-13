#!/usr/bin/env python3
"""Genuine networked BRREG smoke, gated by the V1d trusted-source preflight.

This utility never reports official Builderr v2 evaluator acceptance/score.
Only --run-live authorizes outbound official-source requests.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
import re
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v0_kernel.contracts import parse_batch
from v0_kernel.registry import snapshot_checksum
from v0_kernel.storage import atomic_json, read_jsonl
from v1_official.runner import MODULES, run
from scripts.v1d_prepare_live_smoke import official_reference


def summarize(result: dict[str, Any], rows: list[dict[str, Any]], orgs: list[str],
              source_manifest: dict[str, Any], *, max_requests: int) -> dict[str, Any]:
    exact_membership = len(rows) == len(orgs) and [r.get("organisation_number") for r in rows] == orgs
    official_by_company = [sum(e.get("source_class") == "official_brreg_api"
                               for e in row.get("evidence", []) if isinstance(e, dict))
                           for row in rows if isinstance(row, dict)]
    evidence_total = sum(official_by_company)
    fetch_times = sorted([r.get("operations", {}).get("official_fetch_ms", 0)
                          for r in rows if isinstance(r, dict)])
    state_counts = {m: dict(Counter(row.get("availability", {}).get(m, "MISSING")
                                   for row in rows if isinstance(row, dict))) for m in MODULES}
    requests = result.get("source_requests")
    # Provenance guards must all be true, and output validity is NOT a synonym
    # for a successful network smoke or official scoring readiness.
    mode = source_manifest.get("registry_trust_mode")
    frozen = mode in ("BUNDLED_FROZEN_STARTER_REFERENCE", None) and source_manifest.get("status") == "VERIFIED_LOCAL_INPUTS_ONLY"
    diagnostic = (mode == "UNFROZEN_USER_PINNED_DIAGNOSTIC" and
                  source_manifest.get("status") == "UNFROZEN_DIAGNOSTIC_LOCAL_INPUTS")
    guard = {
        "exact_input_membership_and_order": exact_membership,
        "integrity_pinned_input_manifest": frozen or diagnostic,
        "live_official_transport": result.get("source_mode") == "LIVE_OFFICIAL_ENDPOINTS",
        "successful_completed_run": result.get("run_status") == "completed",
        "locally_validated": result.get("validation", {}).get("passed") is True,
        "official_claim_evidence_present": evidence_total > 0,
        "counted_nonzero_requests": type(requests) is int and 0 < requests <= max_requests,
        "no_global_transport_timeout": result.get("unrecoverable_transport_timeout") is False,
    }
    outcome = ("LIVE_BRREG_FROZEN_SMOKE_PASS" if frozen else "LIVE_BRREG_UNFROZEN_DIAGNOSTIC_PASS")
    return {
        "status": outcome if all(guard.values()) else "LIVE_BRREG_SMOKE_BLOCKED",
        "registry_frozen_reference_matched": frozen,
        "registry_trust_mode": mode or "BUNDLED_FROZEN_STARTER_REFERENCE",
        "checks": guard, "company_count": len(rows), "expected_count": len(orgs),
        "source_requests": requests, "evidence_count": evidence_total,
        "companies_with_official_evidence": sum(c > 0 for c in official_by_company),
        "module_states": state_counts, "wall_clock_ms": result.get("wall_clock_ms"),
        "company_source_fetch_p50_ms": statistics.median(fetch_times) if fetch_times else None,
        "company_source_fetch_p95_ms": fetch_times[min(len(fetch_times) - 1, int(0.95 * (len(fetch_times) - 1)))] if fetch_times else None,
        "partial_research_due_to_budget": result.get("partial_research_due_to_budget"),
        "official_evaluator_v2_schema_verified": False, "official_score": None,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batch", type=Path, required=True)
    p.add_argument("--bulk", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--summary", type=Path, required=True)
    p.add_argument("--max-seconds", type=float, default=1200)
    p.add_argument("--max-requests", type=int, default=500)
    p.add_argument("--evidence-cutoff", type=str)
    p.add_argument("--run-live", action="store_true", help="Explicitly authorize live BRREG API requests")
    p.add_argument("--allow-unfrozen-diagnostic", action="store_true",
                   help="Allow independently pinned newer BRREG CSV for local diagnostics only; not official frozen evaluation")
    args = p.parse_args()
    if not args.run_live:
        print(json.dumps({"status": "LIVE_SMOKE_BLOCKED", "reason": "--run-live required"}))
        return 2
    try:
        paths = [x.resolve(strict=False) for x in (args.batch, args.bulk, args.manifest,
                                                   args.output, args.report, args.summary)]
        if len(paths) != len(set(paths)):
            raise ValueError("Inputs and outputs must be separate paths")
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        trusted = official_reference()
        frozen = manifest.get("registry_trust_mode") == "BUNDLED_FROZEN_STARTER_REFERENCE"
        diagnostic = manifest.get("registry_trust_mode") == "UNFROZEN_USER_PINNED_DIAGNOSTIC"
        if diagnostic and not args.allow_unfrozen_diagnostic:
            raise ValueError("Unfrozen registry needs --allow-unfrozen-diagnostic; never official scoring")
        if not frozen and not diagnostic:
            raise ValueError("Unknown registry source trust mode")
        pinned = trusted.registry_sha256 if frozen else manifest.get("registry_snapshot_sha256")
        if (manifest.get("status") != ("VERIFIED_LOCAL_INPUTS_ONLY" if frozen else "UNFROZEN_DIAGNOSTIC_LOCAL_INPUTS") or
            (frozen and pinned != manifest.get("registry_snapshot_sha256")) or
            not isinstance(pinned, str) or re.fullmatch(r"[a-f0-9]{64}", pinned) is None or
            manifest.get("universe_archive_sha256") != trusted.archive_sha256 or
            manifest.get("universe_content_sha256") != trusted.content_sha256 or
            manifest.get("batch_records") != 100):
            raise ValueError("Untrusted preflight manifest or non-100-company batch")
        if snapshot_checksum(args.bulk) != pinned or snapshot_checksum(args.batch) != manifest.get("batch_sha256"):
            raise ValueError("Sources or batch changed since verified preflight")
        orgs = parse_batch(args.batch)
        if len(orgs) != 100:
            raise ValueError("Live smoke requires 100 companies")
        result = run(organisations=args.batch, bulk=args.bulk, output=args.output,
                     report=args.report, run_id="V1D_REAL_BRREG_100", expected_count=100,
                     expected_bulk_sha256=pinned,
                     allow_live=True, fixture_mode=False,
                     max_seconds=args.max_seconds, max_requests=args.max_requests,
                     checkpoint_every=10, evidence_cutoff=args.evidence_cutoff)
        rows = read_jsonl(args.output)
        summary = summarize(result, rows, orgs, manifest, max_requests=args.max_requests)
        atomic_json(args.summary, summary)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0 if summary["status"] in {"LIVE_BRREG_FROZEN_SMOKE_PASS", "LIVE_BRREG_UNFROZEN_DIAGNOSTIC_PASS"} else 1
    except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError) as exc:
        blocked = {"status": "LIVE_BRREG_SMOKE_BLOCKED", "reason": f"{type(exc).__name__}: {exc}",
                   "official_evaluator_v2_schema_verified": False, "official_score": None}
        # Do not accidentally overwrite source inputs if their paths collide.
        try:
            if args.summary.resolve(strict=False) not in {args.batch.resolve(strict=False), args.bulk.resolve(strict=False), args.manifest.resolve(strict=False)}:
                atomic_json(args.summary, blocked)
        except OSError:
            pass
        print(json.dumps(blocked))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
