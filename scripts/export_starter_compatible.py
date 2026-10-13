#!/usr/bin/env python3
"""Offline, fail-closed export of saved internal results to starter-compatible JSONL."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.batch import canonical_organisation_number, profiles_from_bulk  # noqa: E402
from norway_company_agent.profile_integrity import legacy_pin, validate_metadata, verify_baseline, verify_source_record, verify_claims, verify_receipt  # noqa: E402
from norway_company_agent.evaluator_adapter import adapt_terminal_envelope, validate_starter_envelopes  # noqa: E402
from v0_kernel.storage import atomic_json, atomic_jsonl  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} has invalid JSONL at line {number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path} row {number} is not an object")
            rows.append(value)
    return rows


def export(profiles_path: Path, envelopes_path: Path, output_path: Path, report_path: Path | None = None, *, bulk: Path | None = None, source_dir: Path | None = None, key_path: Path | None = None) -> dict:
    profiles = read_jsonl(profiles_path)
    envelopes = read_jsonl(envelopes_path)
    if len(profiles) != len(envelopes):
        raise ValueError("profiles and envelopes have different row counts")
    expected = []
    trusted_legacy = legacy_pin(profiles_path.read_bytes())
    authoritative = {}
    key = key_path.read_bytes() if key_path else None
    if key is not None and len(key) != 32:
        raise ValueError("Invalid observation authentication key")
    if bulk:
        baseline, _ = profiles_from_bulk(bulk, [p["organisation_number"] for p in profiles])
        authoritative = {p["organisation_number"]: p for p in baseline}
    for index, (profile, envelope) in enumerate(zip(profiles, envelopes), 1):
        profile_org = canonical_organisation_number(profile.get("organisation_number"))
        envelope_org = canonical_organisation_number(envelope.get("organisation_number"))
        if profile_org != envelope_org:
            raise ValueError(f"profile/envelope identity mismatch at position {index}")
        # The exported data must be the profile that was actually checkpointed.
        if envelope.get("profile") != profile:
            raise ValueError(f"profile/envelope provenance mismatch at position {index}")
        expected.append(profile_org)
        validate_metadata(profile)
        if not trusted_legacy:
            if profile_org not in authoritative:
                raise ValueError("Unverifiable persisted baseline: provide --bulk or an approved immutable observation fixture")
            verify_baseline(profile, authoritative[profile_org])
            for field, record in profile["evidence"].items():
                if field not in {"registry", "accounting_obligation"} and not (field == "website" and record.get("source_type") == "legacy_website_crawler_retired") and not (verify_receipt(profile, field, key) or verify_source_record(profile, field, source_dir)):
                    raise ValueError(f"Unverifiable persisted source: {field}; captured bytes required")
        verify_claims(profile, trusted_legacy=trusted_legacy)
    starter = [adapt_terminal_envelope(row) for row in envelopes]
    validation = validate_starter_envelopes(starter, expected)
    if not validation["passed"]:
        raise ValueError("starter-compatible semantic validation failed: " + "; ".join(validation["errors"]))
    atomic_jsonl(output_path, starter)
    result = {
        "status": "completed", "profiles": str(profiles_path), "envelopes": str(envelopes_path),
        "output": str(output_path), "expected_count": len(expected), "validation": validation,
        "organisation_numbers": expected,
        "provenance": "pinned_legacy_observations_raw_bytes_not_retained" if trusted_legacy else "authoritative_registry_and_authenticated_optional_observations",
        "observation_authentication": "private_key_receipts_or_captured_bytes" if key is not None else "captured_bytes_required_for_optional_available_facts",
    }
    if report_path:
        atomic_json(report_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--envelopes", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--bulk", type=Path, help="Authoritative frozen registry for general saved-state export")
    parser.add_argument("--verified-source-snapshots", type=Path, help="Read-only previously captured source bytes")
    parser.add_argument("--observation-key", type=Path, help="Private local key for authentic normalized observations; never publish")
    args = parser.parse_args()
    print(json.dumps(export(args.profiles, args.envelopes, args.output, args.report, bulk=args.bulk, source_dir=args.verified_source_snapshots, key_path=args.observation_key), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
