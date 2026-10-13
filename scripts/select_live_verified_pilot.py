#!/usr/bin/env python3
"""Select deterministic current-data live-verification candidates.

Unlike v1d_prepare_live_smoke.py this tool validates only Builderr's published
universe.  It never substitutes a current registry for the frozen benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from v0_kernel.registry import snapshot_checksum
from v0_kernel.storage import atomic_json, atomic_jsonl
from scripts.v1d_prepare_live_smoke import official_reference, read_verified_universe


def select_candidates(ids: list[str], *, count: int, seed: int) -> list[str]:
    if not isinstance(count, int) or count <= 0 or count > len(ids):
        raise ValueError("Requested candidate count is outside the verified universe")
    if not isinstance(seed, int):
        raise ValueError("Selection seed must be an integer")
    return sorted(ids, key=lambda org: hashlib.sha256(f"{seed}:{org}".encode("ascii")).digest())[:count]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261012)
    args = parser.parse_args()
    if len({path.resolve(strict=False) for path in (args.universe, args.batch, args.manifest)}) != 3:
        raise SystemExit("Universe input, batch output, and manifest output must be distinct")
    try:
        reference = official_reference()
        selected = select_candidates(read_verified_universe(args.universe, reference), count=args.count, seed=args.seed)
        atomic_jsonl(args.batch, [{"organisation_number": org, "sample_slice": "live_verified_candidate"} for org in selected])
        atomic_json(args.manifest, {
            "status": "LIVE_VERIFIED_CANDIDATES_ONLY", "identity_mode": "live_verified",
            "selection_method": "sha256(seed:organisation_number) ascending", "selection_seed": args.seed,
            "universe_archive_sha256": reference.archive_sha256, "universe_content_sha256": reference.content_sha256,
            "universe_records_verified": reference.rows, "candidate_count": len(selected), "candidates": selected,
            "batch_sha256": snapshot_checksum(args.batch), "historical_registry_snapshot_sha256": None,
            "current_live_identity_verified": False,
        })
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "PREFLIGHT_BLOCKED", "reason": str(exc)}))
        return 1
    print(json.dumps({"status": "READY_FOR_AUTHORIZED_LIVE_IDENTITY_VERIFICATION", "candidate_count": len(selected)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
