#!/usr/bin/env python3
"""Prepare a deterministic, stratified *candidate* batch without any HTTP."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.v1d_prepare_live_smoke import official_reference, check_digest  # noqa: E402
from v0_kernel.contracts import is_valid_orgnr  # noqa: E402
from v0_kernel.registry import snapshot_checksum  # noqa: E402
from v0_kernel.storage import atomic_json, atomic_jsonl  # noqa: E402


def employee_band(value: Any) -> str:
    if not isinstance(value, int): return "missing"
    if value == 0: return "0"
    if value < 5: return "1-4"
    if value < 20: return "5-19"
    if value < 100: return "20-99"
    return "100+"


def stratum(row: dict[str, Any]) -> str:
    return "|".join((str(row.get("legal_form") or "missing"),
                     str(row.get("industry_code") or "missing").split(".", 1)[0],
                     str(row.get("municipality_number") or "missing")[:2],
                     employee_band(row.get("employees"))))


def select(universe: Path, *, count: int, seed: int) -> tuple[list[dict[str, str]], dict[str, Any]]:
    ref = official_reference()
    check_digest(universe, ref.archive_sha256, "Builderr universe archive")
    by_stratum: dict[str, tuple[bytes, str, dict[str, Any]]] = {}
    all_ranked: list[tuple[bytes, str, dict[str, Any]]] = []
    content = hashlib.sha256(); rows = 0
    with gzip.open(universe, "rb") as handle:
        for raw in handle:
            content.update(raw); rows += 1
            item = json.loads(raw)
            org = item.get("organisation_number") if isinstance(item, dict) else None
            if not is_valid_orgnr(org):
                raise ValueError(f"invalid organisation number in universe row {rows}")
            rank = hashlib.sha256(f"{seed}:{org}".encode("ascii")).digest()
            item_key = (rank, org, item); key = stratum(item)
            if key not in by_stratum or item_key[:2] < by_stratum[key][:2]:
                by_stratum[key] = item_key
            all_ranked.append(item_key)
    if rows != ref.rows or content.hexdigest() != ref.content_sha256:
        raise ValueError("Builderr universe content integrity check failed")
    chosen: list[tuple[bytes, str, dict[str, Any]]] = []
    seen: set[str] = set()
    # One deterministic representative from as many metadata strata as the
    # requested size permits, then lowest global hash rank for the remainder.
    for item in sorted(by_stratum.values(), key=lambda value: value[:2]):
        if len(chosen) == count: break
        chosen.append(item); seen.add(item[1])
    for item in sorted(all_ranked, key=lambda value: value[:2]):
        if len(chosen) == count: break
        if item[1] not in seen:
            chosen.append(item); seen.add(item[1])
    selected = [{"organisation_number": org, "sample_slice": "current_data_smoke_candidate", "stratum": stratum(row)}
                for _, org, row in chosen]
    return selected, {"seed": seed, "candidate_count": len(selected), "universe_records_verified": rows,
                      "universe_archive_sha256": ref.archive_sha256, "universe_content_sha256": ref.content_sha256,
                      "selection_method": "one lowest sha256-ranked candidate per metadata stratum, then global lowest ranks",
                      "stratum_counts": dict(sorted(Counter(row["stratum"] for row in selected).items()))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20261012)
    args = parser.parse_args()
    if args.count < 1 or args.count > 10000: raise SystemExit("--count must be 1..10000")
    if len({path.resolve(strict=False) for path in (args.universe, args.batch, args.manifest)}) != 3:
        raise SystemExit("universe, batch and manifest paths must be distinct")
    selected, manifest = select(args.universe, count=args.count, seed=args.seed)
    atomic_jsonl(args.batch, selected)
    manifest.update({"status": "CURRENT_DATA_CANDIDATES_ONLY_NO_HTTP", "batch_sha256": snapshot_checksum(args.batch),
                     "nominal_max_enrichment_requests": len(selected) * 5,
                     "live_identity_verified": False})
    atomic_json(args.manifest, manifest)
    print(json.dumps(manifest, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
