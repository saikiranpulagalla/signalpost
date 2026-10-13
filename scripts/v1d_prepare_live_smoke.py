#!/usr/bin/env python3
"""Verify the *actual* Builderr universe and frozen BRREG snapshot; select 100 IDs.

No HTTP, no generated/fabricated source rows, no caller-supplied substitute trust roots.
The bundled starter metadata is the authority for the BRREG snapshot digest.
The two Builderr universe hashes originate in scoring-contract version 2.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v0_kernel.contracts import is_valid_orgnr
from v0_kernel.registry import snapshot_checksum
from v0_kernel.storage import atomic_json, atomic_jsonl

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SHA256 = "1c89710e5b01f8617e86d09fbdff4a52f2f8dbbba297e74f7164b5984f5a0384"
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024  # 1 GiB total, fail closed
MAX_LINE_BYTES = 1024 * 1024  # 1 MiB per universe record
MAX_ROWS = 500_000

@dataclass(frozen=True)
class Reference:
    archive_sha256: str
    content_sha256: str
    registry_sha256: str
    rows: int


def official_reference() -> Reference:
    meta = json.loads((ROOT / "data" / "universe-metadata.json").read_text(encoding="utf-8"))
    return Reference(ARCHIVE_SHA256, meta["master_content_sha256"],
                     meta["registry_snapshot_sha256"], meta["master_rows"])


def check_digest(path: Path, expected: str, label: str) -> str:
    if not path.is_file() or not path.stat().st_size:
        raise ValueError(f"{label}: source missing or empty")
    result = snapshot_checksum(path)
    if result != expected:
        raise ValueError(f"{label}: SHA-256 differs from independently pinned digest")
    return result


def read_verified_universe(path: Path, reference: Reference) -> list[str]:
    check_digest(path, reference.archive_sha256, "Builderr universe archive")
    with path.open("rb") as probe:
        if probe.read(2) != b"\x1f\x8b":
            raise ValueError("Builderr universe is not gzip-compressed")
    ids: list[str] = []
    seen: set[str] = set()
    h = hashlib.sha256()
    total_bytes = 0
    try:
        with gzip.open(path, "rb") as inp:
            for index, line in enumerate(inp, 1):
                total_bytes += len(line)
                if len(line) > MAX_LINE_BYTES or total_bytes > MAX_UNCOMPRESSED_BYTES or index > MAX_ROWS:
                    raise ValueError("Builderr universe exceeds declared resource bounds")
                h.update(line)
                if not line.strip():
                    raise ValueError("Builderr universe contains an empty record")
                item: Any = json.loads(line)
                org = item.get("organisation_number") if isinstance(item, dict) else None
                if not is_valid_orgnr(org):
                    raise ValueError(f"Builderr universe contains invalid org number at row {index}")
                if org in seen:
                    raise ValueError(f"Builderr universe contains duplicate org number at row {index}")
                ids.append(org)
                seen.add(org)
    except (OSError, EOFError) as exc:
        raise ValueError("Builderr universe gzip failed integrity/read checks") from exc
    if h.hexdigest() != reference.content_sha256:
        raise ValueError("Builderr uncompressed universe hash mismatch")
    if len(ids) != reference.rows:
        raise ValueError(f"Builderr universe row count mismatch: {len(ids)} != {reference.rows}")
    return ids


def prepare(universe: Path, registry: Path, output_batch: Path, output_manifest: Path,
            *, reference: Reference, count: int = 100, seed: int = 20261009,
            diagnostic_unfrozen_registry: bool = False) -> dict[str, Any]:
    paths = [p.resolve(strict=False) for p in (universe, registry, output_batch, output_manifest)]
    if len(set(paths)) != len(paths):
        raise ValueError("Input and output paths must all be distinct")
    if not isinstance(count, int) or count <= 0 or not isinstance(seed, int):
        raise ValueError("Invalid smoke-batch selection parameters")
    registry_digest = check_digest(registry, reference.registry_sha256, "Frozen BRREG registry")
    ids = read_verified_universe(universe, reference)
    if count > len(ids):
        raise ValueError("Requested smoke count exceeds verified universe")
    selected = random.Random(seed).sample(ids, count)
    rows = [{"organisation_number": org} for org in selected]
    atomic_jsonl(output_batch, rows)
    batch_sha = snapshot_checksum(output_batch)
    manifest = {
        "status": ("UNFROZEN_DIAGNOSTIC_LOCAL_INPUTS" if diagnostic_unfrozen_registry
                   else "VERIFIED_LOCAL_INPUTS_ONLY"),
        "registry_trust_mode": ("UNFROZEN_USER_PINNED_DIAGNOSTIC" if diagnostic_unfrozen_registry
                                else "BUNDLED_FROZEN_STARTER_REFERENCE"),
        "source_mode": "SOURCE_INTEGRITY_PREFLIGHT_NO_HTTP",
        "universe_archive_sha256": reference.archive_sha256,
        "universe_content_sha256": reference.content_sha256,
        "registry_snapshot_sha256": registry_digest,
        "universe_records_verified": len(ids), "batch_records": count,
        "selection_seed": seed, "batch_sha256": batch_sha,
        "official_network_smoke": "NOT_RUN", "official_evaluator_v2_schema_verified": False,
        "official_score": None,
    }
    atomic_json(output_manifest, manifest)
    return manifest


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--universe", type=Path, required=True, help="Builderr .jsonl.gz universe")
    p.add_argument("--bulk", type=Path, required=True, help="Frozen BRREG CSV or gzipped CSV")
    p.add_argument("--batch", type=Path, required=True, help="Output 100-company JSONL")
    p.add_argument("--manifest", type=Path, required=True, help="Output digest/provenance manifest")
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--seed", type=int, default=20261009)
    p.add_argument("--diagnostic-registry-sha256",
                   help="For CURRENT BRREG bulk only: explicitly pin local diagnostic bytes; NOT the Builderr frozen snapshot")
    args = p.parse_args()
    try:
        ref = official_reference()
        diagnostic = args.diagnostic_registry_sha256 is not None
        if diagnostic:
            if re.fullmatch(r"[a-f0-9]{64}", args.diagnostic_registry_sha256) is None:
                raise ValueError("Invalid diagnostic SHA-256 (64 lowercase hex required)")
            ref = Reference(ref.archive_sha256, ref.content_sha256,
                            args.diagnostic_registry_sha256, ref.rows)
        result = prepare(args.universe, args.bulk, args.batch, args.manifest,
                         reference=ref, count=args.count, seed=args.seed,
                         diagnostic_unfrozen_registry=diagnostic)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "PREFLIGHT_BLOCKED", "reason": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
