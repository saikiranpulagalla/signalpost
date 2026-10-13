"""Offline deadline simulations based on saved request telemetry.

This is a planning model, not a live-network benchmark.  It schedules whole
source-family waves onto a bounded worker heap.  Work which cannot be admitted
before the reserve is deliberately omitted from the plan, rather than being
started and later misrepresented as checked absence.
"""
from __future__ import annotations

import argparse
import heapq
import json
import sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_company_agent.admission import admit_wave


FAMILIES = ("registry_live", "financials", "roles", "locations", "group")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--telemetry", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--reserve-seconds", type=float, default=30.0)
    parser.add_argument("--identity-mode", choices=("live_verified", "frozen_replay"), default="live_verified")
    parser.add_argument("--companies", default="100,1000,1500", help="Comma-separated hypothetical batch sizes")
    args = parser.parse_args()
    counts = [int(value) for value in args.companies.split(",")]
    if not counts or any(value < 1 for value in counts):
        raise SystemExit("Company counts must be positive")
    families = FAMILIES if args.identity_mode == "live_verified" else FAMILIES[1:]
    rows = [json.loads(line) for line in args.telemetry.read_text(encoding="utf-8").splitlines() if line.strip()]
    durations: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        if row.get("source_family") in FAMILIES and row.get("duration_ms") is not None:
            durations[row["source_family"]].append(int(row["duration_ms"]))
    if any(not durations[family] for family in families):
        raise SystemExit("Telemetry lacks one or more required source-family durations")

    def simulate(companies: int, deadline_seconds: int) -> dict:
        worker_ready = [0.0] * args.workers
        heapq.heapify(worker_ready)
        started = 0
        per_family = defaultdict(int)
        omitted = defaultdict(int)
        unfinished_identity = 0
        # Identity is first; subsequent families are globally prioritized.
        for family in families:
            if unfinished_identity:
                omitted[family] = companies
                continue
            # Production joins the entire previous wave before submitting
            # the next. Preserve that barrier even for an odd batch size.
            worker_ready = [max(worker_ready)] * args.workers
            heapq.heapify(worker_ready)
            conservative = sorted(durations[family])[min(len(durations[family])-1, int(len(durations[family])*0.95))] / 1000
            selected, _ = admit_wave([str(i) for i in range(companies)], workers=args.workers,
                expected_seconds=max(conservative, .001), remaining_seconds=max(0, deadline_seconds-args.reserve_seconds-max(worker_ready)),
                request_slots=None, family_slots=None)
            omitted[family] = companies - len(selected)
            if family == "registry_live":
                unfinished_identity = companies - len(selected)
                omitted[family] = 0  # mandatory identity is unfinished, not optional omission
            for company in range(len(selected)):
                ready = heapq.heappop(worker_ready)
                duration = conservative
                if ready + duration > deadline_seconds - args.reserve_seconds:
                    heapq.heappush(worker_ready, ready)
                    omitted[family] += 1
                    omitted[family] += companies - company - 1
                    break
                heapq.heappush(worker_ready, ready + duration)
                started += 1
                per_family[family] += 1
        elapsed = max(worker_ready) if worker_ready else 0.0
        unattempted = companies * len(families) - started
        mandatory_identity_complete = args.identity_mode == "frozen_replay" or per_family["registry_live"] == companies
        return {
            "companies": companies, "deadline_seconds_hypothetical": deadline_seconds,
            "identity_mode": args.identity_mode,
            "finalization_reserve_seconds": args.reserve_seconds, "workers": args.workers,
            "source_starts": started, "source_family_starts": dict(per_family),
            "internal_terminal_envelopes": companies, "intentionally_omitted_by_family": dict(omitted),
            "unattempted_sources": unattempted,
            "simulated_elapsed_seconds": round(elapsed, 3),
            "mandatory_identity_complete": mandatory_identity_complete,
            "admitted_but_unfinished_mandatory_identity": unfinished_identity,
            "publication_fail_closed": not mandatory_identity_complete,
            "result": "PASS" if mandatory_identity_complete else "FAIL",
            "note": "Batch-wide admission simulation from saved live latency distribution; not a network benchmark.",
        }

    payload = {"kind": "offline_telemetry_deadline_simulation", "scenarios": [
        simulate(companies, deadline) for companies in counts for deadline in (300, 600, 1200, 2700)
    ]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
