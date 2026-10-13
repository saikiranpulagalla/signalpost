#!/usr/bin/env python3
"""Bounded, explicitly authorized real-source smoke wrapper."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from norway_company_agent.batch import read_organisation_numbers  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a bounded authorised BRREG Signalpost smoke test")
    parser.add_argument("--organisations", required=True, type=Path)
    parser.add_argument("--identity-mode", choices=("frozen_replay", "live_verified"), default="frozen_replay")
    parser.add_argument("--bulk", type=Path, help="Pinned frozen snapshot; required only for frozen_replay")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--modules", default="registry,accounting_obligation,registry_live,roles,locations,financials,group")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-source-requests", type=int, default=25)
    parser.add_argument("--source-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--source-attempts", type=int, default=1)
    parser.add_argument("--request-telemetry", type=Path,
                        help="New JSONL destination for per-request metadata (no source bodies)")
    parser.add_argument("--source-snapshots-dir", type=Path,
                        help="New permitted-public-response snapshot directory")
    parser.add_argument("--source-snapshot-byte-cap", type=int, default=25_000_000)
    parser.add_argument("--network-policy", choices=("authorized_public_brreg",),
                        default="authorized_public_brreg",
                        help="Live smoke permits only the explicit public BRREG policy")
    parser.add_argument("--output-target", choices=("internal_v2", "starter_compatible"),
                        default="starter_compatible",
                        help="Terminal envelope target; starter-compatible is the smoke default")
    parser.add_argument("--confirm-authorized-live-sources", action="store_true")
    args = parser.parse_args()
    if not args.confirm_authorized_live_sources:
        raise SystemExit("Refusing live requests without --confirm-authorized-live-sources")
    if args.identity_mode == "frozen_replay" and args.bulk is None:
        raise SystemExit("--bulk is required for frozen_replay")
    if args.identity_mode == "live_verified" and args.bulk is not None:
        raise SystemExit("--bulk must not be supplied for live_verified")
    count = len(read_organisation_numbers(args.organisations))
    if not 1 <= count <= 100:
        raise SystemExit("Live smoke accepts 1 through 100 supplied companies")
    if args.workers < 1 or args.workers > 2:
        raise SystemExit("Live smoke permits one or two workers")
    if args.max_source_requests < 0 or args.source_timeout_seconds <= 0 or args.source_attempts != 1:
        raise SystemExit("Live smoke requires a non-negative budget, positive timeout, and one request attempt")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-S", str(ROOT / "scripts" / "run_competition_batch.py"),
               "--organisations", str(args.organisations),
               "--profiles-output", str(args.output_dir / "profiles.jsonl"),
               "--output", str(args.output_dir / "envelopes.jsonl"),
               "--report", str(args.output_dir / "report.json"), "--run-id", args.run_id,
               "--expected-count", str(count), "--modules", args.modules,
               "--workers", str(args.workers),
               "--max-source-requests", str(args.max_source_requests),
               "--source-timeout-seconds", str(args.source_timeout_seconds),
               "--source-attempts", str(args.source_attempts),
               "--output-target", args.output_target]
    if args.request_telemetry is not None:
        command.extend(["--request-telemetry", str(args.request_telemetry)])
    if args.source_snapshots_dir is not None:
        command.extend(["--source-snapshots-dir", str(args.source_snapshots_dir),
                        "--source-snapshot-byte-cap", str(args.source_snapshot_byte_cap)])
    if args.identity_mode == "live_verified":
        command.extend(["--live-bootstrap", "--confirm-authorized-live-sources",
                        "--network-policy", args.network_policy])
    else:
        command.extend(["--bulk", str(args.bulk), "--confirm-authorized-live-sources",
                        "--network-policy", args.network_policy])
    raise SystemExit(subprocess.run(command, cwd=ROOT).returncode)


if __name__ == "__main__":
    main()
