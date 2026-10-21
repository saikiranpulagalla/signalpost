#!/usr/bin/env python3
"""Create a distributable, no-network regression fixture from local live results.

Role-holder names, addresses, phone numbers, source bodies, and source values
are excluded.  Claim/evidence linkage, identities, periods, scopes, digests,
availability, and aggregate counts are retained.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from v0_kernel.storage import atomic_json, atomic_jsonl, read_jsonl  # noqa: E402
from scripts.build_profile_viewer import render  # noqa: E402


SENSITIVE_FIELDS = {"registered_role", "registered_subunit", "registered_address"}


def read_rows(path: Path) -> list[dict]:
    return read_jsonl(path)


def sanitize_profile(profile: dict) -> dict:
    evidence = {}
    for field, record in profile["evidence"].items():
        evidence[field] = {key: record.get(key) for key in ("field", "status", "source_type", "source_class", "source_url", "retrieved_at", "content_sha256", "note")}
    claims = []
    for claim in profile["research_result"]["claims"]:
        value = claim.get("value")
        if claim.get("field") in SENSITIVE_FIELDS:
            value = {"redacted_in_public_fixture": True}
        claims.append({**claim, "value": value})
    result = {"schema": profile["research_result"]["schema"], "organisation_number": profile["organisation_number"],
              "claims": claims, "availability": profile["research_result"]["availability"]}
    return {"organisation_number": profile["organisation_number"], "name": profile.get("name"),
            "legal_form": profile.get("legal_form"), "industry_code": profile.get("industry_code"),
            "industry_label": profile.get("industry_label"), "evidence": evidence, "research_result": result,
            "synthesis": profile["synthesis"], "run_metrics": profile["run_metrics"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--envelopes", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    profiles = [sanitize_profile(row) for row in read_rows(args.profiles)]
    source_envelopes = read_rows(args.envelopes)
    envelopes = []
    for profile, source in zip(profiles, source_envelopes):
        envelopes.append({"organisation_number": profile["organisation_number"], "run_id": source["run_id"],
                          "state": source["state"], "started_at": source["started_at"], "completed_at": source["completed_at"],
                          "modules": source["modules"], "profile": profile})
    report = json.loads(args.report.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    atomic_jsonl(args.output_dir / "profiles.jsonl", profiles)
    atomic_jsonl(args.output_dir / "envelopes.jsonl", envelopes)
    atomic_json(args.output_dir / "report.json", {key: report[key] for key in ("run_id", "expected_count", "operations", "validation", "identity_mode", "modules")})
    atomic_json(args.output_dir / "completion.json", {"envelope_count": len(envelopes),
               "envelope_sha256": hashlib.sha256((args.output_dir / "envelopes.jsonl").read_bytes()).hexdigest(),
               "fixture": "sanitized_no_raw_source_bodies_or_role_holder_names"})
    (args.output_dir / "viewer.html").write_text(render(profiles), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
